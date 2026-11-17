/* MAB Studio private offline GPU viewer. No network access and no world-writing paths. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const canvas = $('view');
  const status = $('status');
  const errorBox = $('error');
  function fail(message) {
    errorBox.style.display = 'block';
    errorBox.textContent = '高精細プレビューを表示できません。\n' + message + '\n標準プレビューはそのまま使用できます。';
    status.textContent = 'GPU表示: 利用不可';
  }
  let data;
  try { data = JSON.parse($('mab-data').textContent); }
  catch (err) { fail('プレビューデータの解析エラー'); return; }
  if (!Array.isArray(data.voxels) || !Array.isArray(data.palette)) { fail('プレビューデータが不正です'); return; }
  const gl = canvas.getContext('webgl', { alpha: false, antialias: true, depth: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
  if (!gl) { fail('WebGLが無効です。Edge/Chromeのハードウェアアクセラレーション設定を確認してください。'); return; }
  const vertexSource = `
    attribute vec3 aPosition; attribute vec3 aNormal; attribute vec3 aColor;
    uniform mat4 uView, uProj;
    varying vec3 vNormal, vColor;
    void main(){ vNormal=aNormal;vColor=aColor;gl_Position=uProj*uView*vec4(aPosition,1.0); }
  `;
  const fragmentSource = `
    precision mediump float;
    varying vec3 vNormal, vColor;
    uniform float uAlpha, uUnlit;
    void main(){
      vec3 n=normalize(vNormal);
      float direct=max(0.0,dot(n,normalize(vec3(-0.44,0.85,0.55))));
      float ambient=0.50+0.16*max(n.y,0.0)+0.10*max(n.x,0.0);
      float shade=mix(ambient+direct*0.31,1.0,uUnlit);
      vec3 linear=pow(vColor,vec3(2.2))*shade;
      gl_FragColor=vec4(pow(max(linear,vec3(0.0)),vec3(1.0/2.2)),uAlpha);
    }
  `;
  function compileShader(type, code) {
    const shader=gl.createShader(type); gl.shaderSource(shader,code);gl.compileShader(shader);
    if (!gl.getShaderParameter(shader,gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    return shader;
  }
  let program;
  try {
    const vs=compileShader(gl.VERTEX_SHADER,vertexSource),fs=compileShader(gl.FRAGMENT_SHADER,fragmentSource);
    program=gl.createProgram();gl.attachShader(program,vs);gl.attachShader(program,fs);gl.linkProgram(program);
    if (!gl.getProgramParameter(program,gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.deleteShader(vs);gl.deleteShader(fs);
  } catch(err) { fail('WebGLシェーダー初期化エラー: '+String(err.message).slice(0,250));return; }
  gl.useProgram(program);
  const attribs = ['aPosition','aNormal','aColor'].map(n=>gl.getAttribLocation(program,n));
  const uniforms = Object.fromEntries(['uView','uProj','uAlpha','uUnlit'].map(n=>[n,gl.getUniformLocation(program,n)]));
  const stride=9*4;
  let mainBuffer=null, mainVertices=0, gridBuffer=null, gridVertices=0;
  const sourceBounds=data.bounds;
  const bounds={...sourceBounds};
  const center=[(bounds.minX+bounds.maxX+1)/2,(bounds.minY+bounds.maxY+1)/2,(bounds.minZ+bounds.maxZ+1)/2];
  const sizes=[bounds.maxX-bounds.minX+1,bounds.maxY-bounds.minY+1,bounds.maxZ-bounds.minZ+1];
  const radius=Math.max(1,Math.hypot(...sizes)/2);
  const halfSizes=sizes.map(v=>v/2);
  // Axis-aligned SOURCE bounding-box corners in GPU-local coordinates.
  // Camera framing uses all eight corners; a bounding sphere wastes substantial
  // screen space on tall buildings or broad, flat roofs.
  const corners=[];
  for(const x of [-halfSizes[0],halfSizes[0]])
    for(const y of [-halfSizes[1],halfSizes[1]])
      for(const z of [-halfSizes[2],halfSizes[2]]) corners.push([x,y,z]);
  // Render near the origin, even when the SOURCE uses far-away world coordinates.
  // This avoids loss of single-block detail in WebGL float32 transforms.
  let yaw=0.78,pitch=0.39,scale=1.0,projection='perspective',target=[0,0,0],slice=null;
  const views={
    iso:[0.78,0.39],front:[0,0],back:[Math.PI,0],
    left:[-Math.PI/2,0],right:[Math.PI/2,0],top:[0,Math.PI/2-0.0001]
  };
  let activeView='iso';
  let shown=0,quads=0;
  const clamp=(x,lo,hi)=>Math.max(lo,Math.min(hi,x));
  function colorFor(mat) {
    const color = data.palette[mat] || '#bdcbd9';
    return [parseInt(color.slice(1,3),16)/255, parseInt(color.slice(3,5),16)/255, parseInt(color.slice(5,7),16)/255];
  }
  const colorCache=new Map();
  function getColor(mat) { if(!colorCache.has(mat)) colorCache.set(mat,colorFor(mat));return colorCache.get(mat); }
  function addVertex(targetArr,p,n,c){
    targetArr.push(p[0]-center[0],p[1]-center[1],p[2]-center[2],...n,...c);
  }
  function appendQuad(arr, corners, normal, col) {
    const order=[0,1,2,0,2,3];
    for(const i of order) addVertex(arr,corners[i],normal,col);
  }
  function recalcMesh() {
    const voxels = slice === null ? data.voxels : data.voxels.filter(p=>p[1]<=slice);
    shown=voxels.length;
    const occupied=new Set(voxels.map(p=>p[0]+','+p[1]+','+p[2]));
    const groups=new Map();
    // name, neighbor offsets, axis, face plane offset
    const dirs=[['xp',1,0,0,'x',1],['xn',-1,0,0,'x',0],['yp',0,1,0,'y',1],['yn',0,-1,0,'y',0],['zp',0,0,1,'z',1],['zn',0,0,-1,'z',0]];
    for(const p of voxels) {
      const [x,y,z,mat]=p;
      for(const [name,dx,dy,dz,axis,offset] of dirs){
        if(occupied.has((x+dx)+','+(y+dy)+','+(z+dz))) continue;
        const plane=(axis==='x'?x:axis==='y'?y:z)+offset;
        const u=axis==='x'?y:x, v=axis==='z'?y:z;
        const key=name+'|'+plane+'|'+mat;
        let group=groups.get(key);
        if(!group){group={name,plane,mat,cells:new Set(),coords:[]};groups.set(key,group);}
        group.cells.add(u+','+v);group.coords.push([u,v]);
      }
    }
    const arr=[];quads=0;
    for(const group of groups.values()){
      const remaining=group.cells;
      group.coords.sort((a,b)=>a[1]-b[1]||a[0]-b[0]);
      for(const [u,v] of group.coords){
        if(!remaining.has(u+','+v))continue;
        let width=1;
        while(remaining.has((u+width)+','+v))width++;
        let height=1;
        grow: while(true){
          for(let du=0;du<width;du++)if(!remaining.has((u+du)+','+(v+height)))break grow;
          height++;
        }
        for(let dv=0;dv<height;dv++) for(let du=0;du<width;du++) remaining.delete((u+du)+','+(v+dv));
        quads++;
        if(quads>150000)throw new Error('表示用の境界面が150,000面を超えました。大きいモデルは断面を選択してください。');
        let corners,normal;
        const plane=group.plane;
        if(group.name[0]==='x'){
          corners=[[plane,u,v],[plane,u+width,v],[plane,u+width,v+height],[plane,u,v+height]];
          normal=[group.name==='xp'?1:-1,0,0];
        } else if(group.name[0]==='y'){
          corners=[[u,plane,v],[u+width,plane,v],[u+width,plane,v+height],[u,plane,v+height]];
          normal=[0,group.name==='yp'?1:-1,0];
        } else {
          corners=[[u,v,plane],[u+width,v,plane],[u+width,v+height,plane],[u,v+height,plane]];
          normal=[0,0,group.name==='zp'?1:-1];
        }
        appendQuad(arr,corners,normal,getColor(group.mat));
      }
    }
    if(mainBuffer)gl.deleteBuffer(mainBuffer);
    mainBuffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,mainBuffer);
    gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(arr),gl.STATIC_DRAW);
    mainVertices=arr.length/9;
    $('scene-stats').textContent=shown.toLocaleString('ja-JP')+'セル / '+quads.toLocaleString('ja-JP')+'境界面 · SOURCE由来 · 表示のみ';
  }
  function recalcGrid(){
    const arr=[];
    const radiusGrid=Math.ceil(Math.max(sizes[0],sizes[2],10)*0.88)+4;
    const half=Math.min(100,Math.max(10,radiusGrid));
    const ground=bounds.minY-0.16;
    const cx=center[0],cz=center[2];
    for(let d=-half;d<=half;d++){
      const k= d%5===0?0.24:0.17, c=[k,k*1.22,k*1.55];
      addVertex(arr,[cx+d,ground,cz-half],[0,1,0],c);
      addVertex(arr,[cx+d,ground,cz+half],[0,1,0],c);
      addVertex(arr,[cx-half,ground,cz+d],[0,1,0],c);
      addVertex(arr,[cx+half,ground,cz+d],[0,1,0],c);
    }
    gridBuffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,gridBuffer);
    gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(arr),gl.STATIC_DRAW);
    gridVertices=arr.length/9;
  }
  function normalize(a){const l=Math.hypot(...a)||1;return a.map(x=>x/l);}
  function cross(a,b){return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];}
  function dot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];}
  function lookAt(eye,center){
    const z=normalize(eye.map((x,i)=>x-center[i])),x=normalize(cross([0,1,0],z)),y=cross(z,x);
    return new Float32Array([x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1]);
  }
  function perspective(fov,aspect,near,far){
    const f=1/Math.tan(fov/2);return new Float32Array([f/aspect,0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0]);
  }
  function ortho(half,aspect,near,far){
    const right=half*aspect;
    return new Float32Array([1/right,0,0,0,0,1/half,0,0,0,0,-2/(far-near),0,0,0,-(far+near)/(far-near),1]);
  }
  function bindBuffer(buffer){
    gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
    for(let i=0;i<3;i++){gl.enableVertexAttribArray(attribs[i]);gl.vertexAttribPointer(attribs[i],3,gl.FLOAT,false,stride,i*12);}
  }
  function resize(){
    const rect=canvas.getBoundingClientRect();
    const dpr=Math.min(2,window.devicePixelRatio||1)*1.12;
    const w=Math.max(1,Math.min(4096,Math.round(rect.width*dpr))), h=Math.max(1,Math.min(4096,Math.round(rect.height*dpr)));
    if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}
    gl.viewport(0,0,w,h);
    return w/h;
  }
  let queued=false;
  function requestDraw(){if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;try{render();}catch(err){fail(String(err.message).slice(0,350));}});}
  function cameraBasis(){
    const sy=Math.sin(yaw),cy=Math.cos(yaw),sp=Math.sin(pitch),cp=Math.cos(pitch);
    return {
      forward:[sy*cp,sp,cy*cp],
      right:[cy,0,-sy],
      up:[-sy*sp,cp,-cy*sp]
    };
  }
  // Fit the actual projected bounding box, not a loose bounding sphere.
  // For each vertex, distance must exceed its camera-forward coordinate plus
  // the horizontal/vertical perspective footprint. Leave 18% safe margin.
  function framing(aspect){
    const halfTan=Math.tan(Math.PI/8),basis=cameraBasis(),safe=0.82;
    let distance=0,orthoHalf=0;
    for(const point of corners){
      const forward=dot(point,basis.forward),x=Math.abs(dot(point,basis.right)),y=Math.abs(dot(point,basis.up));
      distance=Math.max(distance,forward+Math.max(x/(halfTan*aspect*safe),y/(halfTan*safe)));
      orthoHalf=Math.max(orthoHalf,x/(aspect*safe),y/safe);
    }
    return {distance:Math.max(0.5,distance)/scale,orthoHalf:Math.max(0.5,orthoHalf)/scale};
  }
  function render(){
    const aspect=resize();
    const {distance,orthoHalf}=framing(aspect);
    const eye=[target[0]+distance*Math.sin(yaw)*Math.cos(pitch),target[1]+distance*Math.sin(pitch),target[2]+distance*Math.cos(yaw)*Math.cos(pitch)];
    const far=Math.max(100,distance+radius*12);
    const proj=projection==='perspective'?perspective(Math.PI/4,aspect,0.05,far):ortho(orthoHalf,aspect,0.05,far);
    gl.uniformMatrix4fv(uniforms.uProj,false,proj);gl.uniformMatrix4fv(uniforms.uView,false,lookAt(eye,target));
    gl.clearColor(0.065,0.103,0.166,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LEQUAL);gl.disable(gl.CULL_FACE);
    gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
    if(gridBuffer){gl.uniform1f(uniforms.uAlpha,0.27);gl.uniform1f(uniforms.uUnlit,1);bindBuffer(gridBuffer);gl.drawArrays(gl.LINES,0,gridVertices);}
    gl.disable(gl.BLEND);
    if(mainBuffer){gl.uniform1f(uniforms.uAlpha,1);gl.uniform1f(uniforms.uUnlit,0);bindBuffer(mainBuffer);gl.drawArrays(gl.TRIANGLES,0,mainVertices);}
  }
  function markView(name){
    activeView=name;
    for(const key of Object.keys(views)){
      const button=$('view-'+key);
      button.classList.toggle('active',key===name);
      button.setAttribute('aria-pressed',String(key===name));
    }
  }
  function selectView(name){
    if(!(name in views))return;
    [yaw,pitch]=views[name];scale=1;target=[0,0,0];markView(name);requestDraw();
  }
  function reset(){selectView('iso');}
  function chooseProjection(value){
    projection=value;
    for(const val of ['perspective','orthographic']){$(val).classList.toggle('active',val===value);$(val).setAttribute('aria-pressed',String(val===value));}
    requestDraw();
  }
  $('reset').addEventListener('click',reset);
  for(const name of Object.keys(views)) $('view-'+name).addEventListener('click',()=>selectView(name));
  canvas.addEventListener('dblclick',reset);
  document.addEventListener('keydown',ev=>{
    // Never intercept keystrokes used to operate the Y section slider.
    const tag=ev.target && ev.target.tagName;
    if(tag==='INPUT'||tag==='TEXTAREA'||ev.ctrlKey||ev.altKey||ev.metaKey)return;
    if(ev.key.toLowerCase()==='f'){reset();ev.preventDefault();}
    const keys={'1':'iso','2':'front','3':'back','4':'left','5':'right','6':'top'};
    if(keys[ev.key]){selectView(keys[ev.key]);ev.preventDefault();}
  });
  $('perspective').addEventListener('click',()=>chooseProjection('perspective'));
  $('orthographic').addEventListener('click',()=>chooseProjection('orthographic'));
  const initialSection=data.sectionLevel;
  const slider=$('slice');slider.min=String(bounds.minY-1);slider.max=String(bounds.maxY);slider.value=String(initialSection===null?bounds.maxY:initialSection);
  slice=initialSection;
  function updateSection(){
    const val=Number(slider.value);
    slice=val>=bounds.maxY?null:val;
    $('slice-value').textContent=slice===null?'全体':'Y ≤ '+slice;
    try{recalcMesh();requestDraw();}catch(err){fail('断面の再描画に失敗: '+String(err.message).slice(0,200));}
  }
  slider.addEventListener('input',updateSection);
  $('all').addEventListener('click',()=>{slider.value=String(bounds.maxY);updateSection();});
  canvas.addEventListener('wheel',ev=>{
    ev.preventDefault();
    const rect=canvas.getBoundingClientRect();
    const aspect=rect.width/Math.max(1,rect.height);
    const before=framing(aspect);
    const next=clamp(scale*Math.exp(-ev.deltaY*0.001),0.2,10);
    if(next===scale)return;
    scale=next;
    const after=framing(aspect);
    const basis=cameraBasis();
    const mx=Number.isFinite(ev.clientX)?ev.clientX:rect.left+rect.width/2;
    const my=Number.isFinite(ev.clientY)?ev.clientY:rect.top+rect.height/2;
    const nx=2*(mx-rect.left)/Math.max(1,rect.width)-1;
    const ny=1-2*(my-rect.top)/Math.max(1,rect.height);
    const oldHalf=projection==='perspective'?before.distance*Math.tan(Math.PI/8):before.orthoHalf;
    const newHalf=projection==='perspective'?after.distance*Math.tan(Math.PI/8):after.orthoHalf;
    const offset=oldHalf-newHalf;
    // Keep the point on the camera-target plane under the pointer in place.
    target=target.map((v,i)=>v+offset*(nx*aspect*basis.right[i]+ny*basis.up[i]));
    requestDraw();
  },{passive:false});
  let drag=null;
  canvas.addEventListener('pointerdown',ev=>{drag={x:ev.clientX,y:ev.clientY,yaw,pitch,target:target.slice(),pan:ev.shiftKey||ev.button===1||ev.button===2};canvas.setPointerCapture(ev.pointerId);canvas.classList.add('dragging');});
  canvas.addEventListener('pointerup',()=>{drag=null;canvas.classList.remove('dragging');});
  canvas.addEventListener('pointercancel',()=>{drag=null;canvas.classList.remove('dragging');});
  canvas.addEventListener('contextmenu',ev=>ev.preventDefault());
  canvas.addEventListener('pointermove',ev=>{
    if(!drag)return;
    const dx=ev.clientX-drag.x,dy=ev.clientY-drag.y;
    if(!drag.pan){yaw=drag.yaw-dx*0.008;pitch=clamp(drag.pitch+dy*0.006,-1.55,1.55);markView(null);}
    else{
      const aspect=canvas.width/Math.max(1,canvas.height);
      const {distance,orthoHalf}=framing(aspect);
      const worldPerPixel=(projection==='perspective'?2*distance*Math.tan(Math.PI/8):orthoHalf*2)/Math.max(1,canvas.clientHeight);
      const right=[Math.cos(yaw),0,-Math.sin(yaw)];
      const up=[-Math.sin(pitch)*Math.sin(yaw),Math.cos(pitch),-Math.sin(pitch)*Math.cos(yaw)];
      target=drag.target.map((v,i)=>v-dx*worldPerPixel*right[i]+dy*worldPerPixel*up[i]);
    }
    requestDraw();
  });
  $('save').addEventListener('click',()=>{
    try{render();const a=document.createElement('a');a.download='MABStudio_GPU_Preview.png';a.href=canvas.toDataURL('image/png');a.click();}
    catch(err){fail('PNG保存に失敗: '+String(err.message).slice(0,200));}
  });
  window.addEventListener('resize',requestDraw);
  if('ResizeObserver' in window)new ResizeObserver(requestDraw).observe(canvas);
  canvas.addEventListener('webglcontextlost',ev=>{ev.preventDefault();fail('GPUコンテキストが失われました。ビューアを再度開いてください。');});
  try{
    recalcGrid();updateSection();markView('iso');
    $('scene-name').textContent=data.title||'建築プレビュー';
    status.textContent='WebGL GPUビューア · 高DPI · 全方向の境界面メッシュ · オフライン';
    document.documentElement.dataset.ready='true';
  }catch(err){fail('3Dメッシュ初期化エラー: '+String(err.message).slice(0,250));}
})();
