// GPU-independent contract smoke: exercises the actual viewer JavaScript.
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.join(__dirname,'..','app','gpu_viewer_assets','viewer.js');
const js=fs.readFileSync(root,'utf8');
const state={buffers:[],draws:[],listeners:{}};
const gl={
  VERTEX_SHADER:35633,FRAGMENT_SHADER:35632,COMPILE_STATUS:35713,LINK_STATUS:35714,
  FLOAT:5126,ARRAY_BUFFER:34962,STATIC_DRAW:35044,TRIANGLES:4,LINES:1,
  COLOR_BUFFER_BIT:16384,DEPTH_BUFFER_BIT:256,DEPTH_TEST:2929,LEQUAL:515,CULL_FACE:2884,
  BLEND:3042,SRC_ALPHA:770,ONE_MINUS_SRC_ALPHA:771,
  createShader:()=>({}), shaderSource(){},compileShader(){},getShaderParameter:()=>true,getShaderInfoLog:()=>'',
  createProgram:()=>({}),attachShader(){},linkProgram(){},getProgramParameter:()=>true,
  getProgramInfoLog:()=>'',deleteShader(){},deleteProgram(){},useProgram(){},
  getAttribLocation:(p,name)=>['aPosition','aNormal','aColor'].indexOf(name),getUniformLocation:(p,name)=>name,
  createBuffer:()=>({}),deleteBuffer(){},bindBuffer(t,b){state.buffer=b},bufferData(t,arr){state.buffers.push({buffer:state.buffer,arr});},
  enableVertexAttribArray(){},vertexAttribPointer(){},uniform1f(){},uniformMatrix4fv(){},viewport(){},
  clearColor(){},clear(){},enable(){},disable(){},depthFunc(){},blendFunc(){},
  drawArrays(mode,first,count){state.draws.push({mode,count});}
};
const bounds={minX:0,maxX:1,minY:0,maxY:1,minZ:0,maxZ:0};
const data={voxels:[[0,0,0,0],[1,0,0,0],[0,1,0,1],[1,1,0,1]],
            bounds,palette:['#aabbcc','#66ccdd'],title:'GPU smoke',sectionLevel:null};
const elements={};
function element(id){
  if(elements[id])return elements[id];
  const obj={id,textContent:'',style:{},classList:{toggle(){}},listeners:{},
    addEventListener(event,cb){this.listeners[event]=cb},setAttribute(){},click(){},
    getBoundingClientRect(){return {width:1000,height:750}},clientHeight:750,
    setPointerCapture(){},toDataURL(){return 'data:image/png;base64,aA=='},
  };
  if(id==='view')obj.getContext=()=>gl;
  if(id==='mab-data')obj.textContent=JSON.stringify(data);
  elements[id]=obj;return obj;
}
const document={getElementById:element,documentElement:{dataset:{}},createElement:()=>({click(){this.clicked=true}})};
const context={document,window:{devicePixelRatio:1,addEventListener(){},},
  ResizeObserver:class{observe(){}},requestAnimationFrame:cb=>cb(),console,
  Float32Array,Map,Set,Number,Math,JSON,Array,parseInt,Error,String};
vm.runInNewContext(js,context,{filename:'viewer.js'});
assert.equal(document.documentElement.dataset.ready,'true');
assert.equal(element('status').textContent.includes('WebGL GPUビューア'),true);
assert.match(element('scene-stats').textContent,/4セル \/ 10境界面/);
assert.equal(state.draws.at(-1).mode,gl.TRIANGLES);
assert.equal(state.draws.at(-1).count,10*6);
// Slice y<=0 exposes a single 2x1x1 prism; still six sides.
element('slice').value='0';element('slice').listeners.input();
assert.match(element('scene-stats').textContent,/2セル \/ 6境界面/);
// Return full, camera and PNG controls remain functional.
element('all').listeners.click();
assert.match(element('scene-stats').textContent,/4セル/);
element('orthographic').listeners.click();
element('perspective').listeners.click();
element('reset').listeners.click();
element('view').listeners.wheel({preventDefault(){},deltaY:100});
element('save').listeners.click();
console.log('PASS_GPU_VIEWER_JS_MESH_AND_CONTROLS');
