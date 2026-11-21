/**
 * MAB Studio -> Minecraft Education placement bridge.
 * Converts an already B143-verified scene to compact, deterministic fill/setblock
 * commands. B143 remains a design-only compiler; it is never patched.
 *
 * Usage: node build.mjs --self-test | --plan PAYLOAD.json | --listen PAYLOAD.json
 * A listener only writes to the world after the active Education player sends
 * the exact !mab-build chat message in the connected world.
 */
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';

export const PORT=8001;
export const BUILD_TOKEN='!mab-build';
export const SCHEMA='mab-studio-education-placement-v1';
export const MATERIALS=Object.freeze([
  'minecraft:stone','minecraft:white_concrete','minecraft:iron_block',
  'minecraft:glass','minecraft:oak_planks','minecraft:glowstone',
  'minecraft:orange_concrete'
]);
const LAYERS=Object.freeze(['B143_STONE','B143_CONCRETE','B143_METAL',
  'B143_GLASS','B143_WOOD','B143_GLOW','B143_ACCENT']);
const AIR='minecraft:air';
const MAX_VOXELS=160000;
const MAX_FILL=32768;
const MAX_COMMANDS=MAX_VOXELS;
const fail=(reason)=>{throw new Error('EDUCATION_BUILD_'+reason);};
const integer=(n)=>Number.isSafeInteger(n);
const hex64=(s)=>typeof s==='string'&&/^[a-f0-9]{64}$/.test(s);

export function validatePayload(p) {
  if(!p || typeof p!=='object' || Array.isArray(p) || p.schema!==SCHEMA ||
      !hex64(p.sourceSha256) || !hex64(p.geometrySha256) ||
      !Array.isArray(p.layerNames) || p.layerNames.length!==LAYERS.length ||
      p.layerNames.some((n,i)=>n!==LAYERS[i]))fail('SOURCE_FORMAT');
  if(p.placementMode!==undefined && !['player','manual'].includes(p.placementMode))fail('PLACEMENT_MODE');
  const o=p.origin;
  if(!o || !integer(o.x)||!integer(o.y)||!integer(o.z)||
      Math.abs(o.x)>29999871||Math.abs(o.z)>29999871||o.y< -64||o.y>319)
    fail('ORIGIN');
  if(!Array.isArray(p.primitives) || p.primitives.length<1 ||
      p.primitives.length>MAX_VOXELS || p.voxels!==p.primitives.length)
    fail('VOXEL_COUNT');
  const seen=new Set();
  for(const c of p.primitives) {
    if(!Array.isArray(c) || c.length!==5 || c.some(x=>!integer(x)) ||
        c[0]<0||c[0]>=128||c[1]<0||c[1]>=96||c[2]<0||c[2]>=128||
        c[3]<0||c[3]>=LAYERS.length || c[4]!==Math.floor(c[1]/5))fail('CELL');
    const k=`${c[0]},${c[1]},${c[2]}`;
    if(seen.has(k))fail('DUPLICATE_CELL');
    seen.add(k);
    if(o.y+c[1]>319 || Math.abs(o.x+c[0])>30000000 ||
       Math.abs(o.z+c[2])>30000000)fail('WORLD_HEIGHT_OR_POSITION');
  }
  return p;
}

/** Greedily merge X runs, adjacent Z rows, then identical Y rectangles. */
function compileCells(p,cells) {
  const rows=new Map();
  for(const [x,y,z,m] of cells){
    const k=`${y}|${z}`;
    if(!rows.has(k))rows.set(k,[]);
    rows.get(k).push([x,m]);
  }
  const xRuns=[];
  for(const [k,row] of rows){
    const [y,z]=k.split('|').map(Number);
    row.sort((a,b)=>a[0]-b[0]);
    let start=row[0][0],end=start,material=row[0][1];
    for(const [x,m] of row.slice(1)){
      if(x===end+1 && m===material){end=x;continue;}
      xRuns.push({x1:start,x2:end,y1:y,y2:y,z1:z,z2:z,m:material});
      start=end=x;material=m;
    }
    xRuns.push({x1:start,x2:end,y1:y,y2:y,z1:z,z2:z,m:material});
  }
  xRuns.sort((a,b)=>a.y1-b.y1||a.z1-b.z1||a.x1-b.x1||a.m-b.m);
  const zRects=[];
  const zActive=new Map();
  for(const r of xRuns){
    const k=`${r.y1}|${r.x1}|${r.x2}|${r.m}`;
    const prev=zActive.get(k);
    if(prev && prev.z2+1===r.z1){prev.z2=r.z2;}
    else{const fresh={...r};zRects.push(fresh);zActive.set(k,fresh);}
  }
  zRects.sort((a,b)=>a.y1-b.y1||a.z1-b.z1||a.x1-b.x1);
  const solids=[];
  const yActive=new Map();
  for(const r of zRects){
    const k=`${r.x1}|${r.x2}|${r.z1}|${r.z2}|${r.m}`;
    const prev=yActive.get(k);
    if(prev && prev.y2+1===r.y1){prev.y2=r.y2;}
    else{const fresh={...r};solids.push(fresh);yActive.set(k,fresh);}
  }
  const commands=[];
  const origin=p.origin;
  let blocks=0;
  for(const s of solids){
    const area=(s.x2-s.x1+1)*(s.z2-s.z1+1);
    const height=Math.max(1,Math.floor(MAX_FILL/area));
    for(let y=s.y1;y<=s.y2;y+=height){
      const y2=Math.min(s.y2,y+height-1);
      const n=area*(y2-y+1);
      const a=[origin.x+s.x1,origin.y+y,origin.z+s.z1];
      const b=[origin.x+s.x2,origin.y+y2,origin.z+s.z2];
      const block=s.m===MATERIALS.length ? AIR : MATERIALS[s.m];
      const command=n===1 ? `setblock ${a.join(' ')} ${block}` :
        `fill ${a.join(' ')} ${b.join(' ')} ${block}`;
      commands.push({command,blocks:n});
      blocks+=n;
      if(commands.length>MAX_COMMANDS)fail('COMMAND_BUDGET');
    }
  }
  if(blocks!==cells.length)fail('CELL_ACCOUNTING');
  return {commands,blocks,origin,sourceSha256:p.sourceSha256,
    geometrySha256:p.geometrySha256};
}

/** Full first build still uses only the verified B143 primitives. */
export function plan(p) {
  validatePayload(p);
  return compileCells(p,p.primitives);
}

/** Re-place only the changes for this project's known site.
 * On a partially failed build, overwrite every potentially touched voxel.
 */
export function planRevision(p,previous=null,uncertain=new Set()) {
  validatePayload(p);
  if(previous) {
    validatePayload(previous);
    if(['x','y','z'].some(axis=>p.origin[axis]!==previous.origin[axis]))
      fail('REVISION_ORIGIN');
  }
  if(!(uncertain instanceof Set))fail('UNCERTAIN_CELLS');
  const old=new Map((previous?.primitives??[]).map(c=>[c.slice(0,3).join(','),c]));
  const next=new Map(p.primitives.map(c=>[c.slice(0,3).join(','),c]));
  const edits=[];
  let added=0,replaced=0,removed=0;
  for(const [key,c] of next){
    const before=old.get(key);
    if(uncertain.has(key) || !before || before[3]!==c[3]) {
      edits.push(c);
      if(!before)added++;
      else if(before[3]!==c[3])replaced++;
    }
  }
  for(const key of new Set([...old.keys(),...uncertain])){
    if(next.has(key))continue;
    const xyz=key.split(',').map(Number);
    if(xyz.length!==3||xyz.some(n=>!integer(n)))fail('UNCERTAIN_CELL_KEY');
    edits.push([xyz[0],xyz[1],xyz[2],MATERIALS.length,Math.floor(xyz[1]/5)]);
    removed++;
  }
  const compiled=compileCells(p,edits);
  return {...compiled,changes:{added,replaced,removed},isRevision:true};
}
function report(type,body={}){process.stdout.write(JSON.stringify({type,...body})+'\n');}
function exitSoon(code){setTimeout(()=>process.exit(code),75).unref();}
function readPayload(file){
  if(typeof file!=='string' || !file || !fs.statSync(file).isFile() ||
      fs.statSync(file).size>9*1024*1024)fail('PAYLOAD_FILE');
  return JSON.parse(fs.readFileSync(file,'utf8'));
}

/** A few read-only block probes bind a saved site to the currently connected
 * world, without assuming that the SocketBE connection ID survives a restart.
 */
export function resumeSamples(previous,target=previous,uncertain=new Set()) {
  validatePayload(previous);
  validatePayload(target);
  if(!(uncertain instanceof Set))fail('RESUME_UNCERTAIN');
  const targetMaterials=new Map(target.primitives.map(c=>[c.slice(0,3).join(','),c[3]]));
  // A partially completed revision may already have changed its edited cells.
  // Check only anchors whose material is unchanged in BOTH designs.
  const cells=previous.primitives.filter(c=>{
    const key=c.slice(0,3).join(',');
    return !uncertain.has(key) && targetMaterials.get(key)===c[3];
  });
  if(!cells.length)fail('RESUME_SITE_NO_STABLE_ANCHOR');
  const indices=[0,Math.floor((cells.length-1)/4),Math.floor((cells.length-1)/2),
    Math.floor(3*(cells.length-1)/4),cells.length-1];
  return [...new Set(indices)].map(i=>cells[i]);
}
export async function verifyResumeSite(world,previous,target=previous,uncertain=new Set()) {
  for(const [x,y,z,m] of resumeSamples(previous,target,uncertain)){
    const o=previous.origin;
    const command=`testforblock ${o.x+x} ${o.y+y} ${o.z+z} ${MATERIALS[m]}`;
    let response;
    try {response=await world.runCommand(command,{timeout:8000,noResponse:false});}
    catch {fail('RESUME_SITE_READBACK_FAILED');}
    if(!response || !integer(response.statusCode) ||
        response.statusCode<0 || response.successCount!==1)
      fail('RESUME_SITE_NOT_MATCHED');
  }
}
function uncertainKeys(input) {
  if(!Array.isArray(input) || input.length>MAX_VOXELS*2)fail('RESUME_UNCERTAIN');
  const result=new Set();
  for(const key of input) {
    if(typeof key!=='string'||!/^(?:0|[1-9][0-9]*),(?:0|[1-9][0-9]*),(?:0|[1-9][0-9]*)$/.test(key))
      fail('RESUME_UNCERTAIN');
    const [x,y,z]=key.split(',').map(Number);
    if(x>=128||y>=96||z>=128)fail('RESUME_UNCERTAIN');
    result.add(key);
  }
  return result;
}
export function commandSucceeded(result){
  // SocketBE CommandResult supplies statusCode, not a guaranteed successCount.
  // The same nonnegative status contract is used by its World.fillBlocks API.
  return result && result.transportError!==true &&
    Number.isInteger(result.statusCode) && result.statusCode>=0;
}
export async function perform(world,compiled,onProgress=()=>{}){
  if(!world || typeof world.runCommand!=='function')fail('WORLD');
  let completed=0,confirmedBlocks=0;
  for(const entry of compiled.commands){
    let response;
    try{response=await world.runCommand(entry.command,{timeout:12000,noResponse:false});}
    catch(error){
      const e=new Error('COMMAND_TRANSPORT_'+String(error?.message??'unknown').slice(0,160));
      e.completed=completed;e.confirmedBlocks=confirmedBlocks;throw e;
    }
    if(!commandSucceeded(response)){
      const e=new Error('COMMAND_REJECTED_'+String(response?.statusMessage??'unknown').slice(0,160));
      e.completed=completed;e.confirmedBlocks=confirmedBlocks;throw e;
    }
    completed++;confirmedBlocks+=entry.blocks;
    if(completed===compiled.commands.length || completed%20===0)
      onProgress({completed,commands:compiled.commands.length,confirmedBlocks});
  }
  return {completed,commands:compiled.commands.length,confirmedBlocks};
}
async function listen(payload){
  const compiled=plan(payload); // parse, validate, and build all commands before opening the port
  const {Server,ServerEvent}=await import('socket-be');
  const server=new Server({port:PORT,webSocketOptions:{host:'127.0.0.1',maxPayload:65536}});
  let started=false;
  const timer=setTimeout(()=>{
    if(!started){report('timeout',{message:'NO_GAME_CONNECTION_OR_CHAT',completed:0});exitSoon(3);}
  },180000);
  server.on(ServerEvent.Open,()=>report('listening',{
    host:'127.0.0.1',port:PORT,token:BUILD_TOKEN,
    commands:compiled.commands.length,blocks:compiled.blocks,
    origin:compiled.origin
  }));
  server.on(ServerEvent.PlayerChat,async ev=>{
    if(started || ev?.message!==BUILD_TOKEN || !ev?.world)return;
    started=true;clearTimeout(timer);
    report('started',{commands:compiled.commands.length,blocks:compiled.blocks});
    try{
      const result=await perform(ev.world,compiled,progress=>report('progress',progress));
      report('result',{
        ...result,sourceSha256:compiled.sourceSha256,
        geometrySha256:compiled.geometrySha256,
        note:'Minecraft command replies confirmed; independent in-world readback not performed'
      });
      exitSoon(0);
    }catch(error){
      report('error',{message:String(error?.message??'error').slice(0,240),
        completed:error?.completed??0,confirmedBlocks:error?.confirmedBlocks??0,
        partialWorldMayRemain:true});
      exitSoon(2);
    }
  });
}
/** Always-on bridge. Education connects once with /wsserver localhost:8001.
 * Subsequent Studio button clicks use stdin, never an in-game chat token.
 */
async function serve(){
  const {Server,ServerEvent}=await import('socket-be');
  const {createInterface}=await import('node:readline');
  const server=new Server({port:PORT,webSocketOptions:{host:'127.0.0.1',maxPayload:65536}});
  let busy=false;
  const deployments=new Map(); // projectId -> site in the live world
  server.on(ServerEvent.Open,()=>report('listening',{port:PORT,
    connectCommand:`/wsserver 127.0.0.1:${PORT}`}));
  const reader=createInterface({input:process.stdin,crlfDelay:Infinity});
  reader.on('line',async line=>{
    if(line.length>9*1024*1024){report('error',{message:'PAYLOAD_TOO_LARGE'});return;}
    if(busy){report('error',{message:'BUILD_ALREADY_RUNNING'});return;}
    busy=true;
    let compiled,payload,projectId,prior,world;
    try {
      const request=JSON.parse(line);
      if(request?.action!=='build')fail('UNKNOWN_ACTION');
      projectId=request.projectId;
      if(projectId!==undefined &&
          (typeof projectId!=='string'||!/^[a-f0-9]{32}$/.test(projectId)))
        fail('PROJECT_ID');
      const worlds=server.getWorlds();
      if(worlds.length!==1){
        report('error',{message:worlds.length===0 ?
          'MINECRAFT_NOT_CONNECTED: /wsserver 127.0.0.1:8001' :
          'MULTIPLE_WORLDS_CONNECTED',completed:0,confirmedBlocks:0});
        return;
      }
      world=worlds[0];
      prior=projectId ? deployments.get(projectId) : null;
      if(prior && prior.world!==world)prior=null;
      payload=request.payload;
      const resume=(!prior && projectId && request.resume &&
        typeof request.resume==='object') ? request.resume : null;
      const old=resume?.previous ? validatePayload(resume.previous) : null;
      if(payload?.placementMode==='player'){
        let origin;
        if(prior)origin=prior.origin;
        else if(old)origin=old.origin;
        else {
          const player=await world.getLocalPlayer();
          const position=await player.getLocation();
          if(!position || !['x','y','z'].every(a=>Number.isFinite(position[a])))
            fail('PLAYER_POSITION_UNAVAILABLE');
          origin={x:Math.floor(position.x)+8,y:Math.floor(position.y)-1,
            z:Math.floor(position.z)+8};
        }
        payload={...payload,origin};
      }
      validatePayload(payload);
      if(prior && ['x','y','z'].some(axis=>prior.origin[axis]!==payload.origin[axis]))
        prior=null;
      if(!prior && old && ['x','y','z'].every(axis=>old.origin[axis]===payload.origin[axis])){
        // Zero writes when the saved site cannot be matched by read-only probes.
        const uncertain=uncertainKeys(resume.uncertain??[]);
        await verifyResumeSite(world,old,payload,uncertain);
        prior={world,origin:old.origin,payload:old,uncertain};
      }
      compiled=prior ? planRevision(payload,prior.payload,prior.uncertain) : plan(payload);
      report('started',{commands:compiled.commands.length,blocks:compiled.blocks,
        origin:compiled.origin});
      const result=await perform(world,compiled,p=>report('progress',p));
      if(projectId)deployments.set(projectId,{
        world,origin:payload.origin,payload,uncertain:new Set()});
      report('result',{...result,sourceSha256:compiled.sourceSha256,
        geometrySha256:compiled.geometrySha256,origin:compiled.origin,
        plannedBlocks:compiled.blocks,targetCells:payload.voxels,
        changes:compiled.changes??{added:payload.voxels,replaced:0,removed:0},
        readbackVerified:false});
    }catch(error){
      // Transport errors can happen after the world was partially modified.
      if(projectId && payload && world && compiled && compiled.commands.length){
        const record=prior??{world,origin:payload.origin,payload:null,uncertain:new Set()};
        for(const c of payload.primitives)
          record.uncertain.add(c.slice(0,3).join(','));
        deployments.set(projectId,record);
      }
      report('error',{message:String(error?.message??'error').slice(0,240),
        completed:error?.completed??0,confirmedBlocks:error?.confirmedBlocks??0,
        partialWorldMayRemain:true});
    }finally{busy=false;}
  });
  reader.on('close',async()=>{await server.stop();process.exit(0);});
}
async function selfTest(){
  const sha='a'.repeat(64);
  const payload={schema:SCHEMA,sourceSha256:sha,geometrySha256:sha,
    origin:{x:100,y:63,z:-10},layerNames:[...LAYERS],voxels:5,
    primitives:[[0,0,0,0,0],[1,0,0,0,0],[0,0,1,0,0],[1,0,1,0,0],[3,1,0,4,0]]};
  const compiled=plan(payload);
  const calls=[];
  const actual=await perform({runCommand:async command=>{
    calls.push(command);return {statusCode:0};
  }},compiled);
  if(compiled.blocks!==5||compiled.commands.length!==2||actual.confirmedBlocks!==5||
      calls[0]!=='fill 100 63 -10 101 63 -9 minecraft:stone'||
      calls[1]!=='setblock 103 64 -10 minecraft:oak_planks')fail('SELF_TEST');
  const revised={...payload,geometrySha256:'b'.repeat(64),
    primitives:payload.primitives.filter(c=>!(c[0]===1&&c[2]===1)&&c[0]!==3)
      .concat([[3,1,0,2,0],[4,1,0,3,0]]),voxels:5};
  const delta=planRevision(revised,payload);
  if(delta.blocks!==3||delta.changes.removed!==1||
      delta.changes.replaced!==1||delta.changes.added!==1||
      !delta.commands.some(c=>c.command.includes('minecraft:air'))||
      !delta.commands.some(c=>c.command.includes('minecraft:iron_block')))
    fail('REVISION_SELF_TEST');
  const recovered=planRevision(revised,payload,new Set(['7,1,0']));
  if(recovered.blocks!==4||!recovered.commands.some(c=>c.command.includes('minecraft:air')))
    fail('PARTIAL_REPAIR_SELF_TEST');
  let probes=0;
  await verifyResumeSite({runCommand:async command=>{
    if(!command.startsWith('testforblock '))fail('RESUME_PROBE_NOT_READONLY');
    probes++;
    return {statusCode:0,successCount:1};
  }},payload);
  if(probes!==5)fail('RESUME_PROBE_COUNT');
  let rejected=false;
  try {await verifyResumeSite({runCommand:async()=>({statusCode:0,successCount:0})},payload);}
  catch(e){rejected=String(e.message).includes('RESUME_SITE_NOT_MATCHED');}
  if(!rejected)fail('RESUME_PROBE_UNMATCHED_ACCEPTED');
  // A previously interrupted revision may have changed one of the old anchors.
  // Ignore it on resume instead of incorrectly rejecting the still-valid site.
  const uncertain=new Set(['0,0,0']);
  const remaining=resumeSamples(payload,payload,uncertain);
  if(remaining.length!==4 || remaining.some(c=>c[0]===0&&c[2]===0))
    fail('RESUME_UNCERTAIN_ANCHOR_USED');
  let recoveryProbes=0;
  await verifyResumeSite({runCommand:async cmd=>{
    if(cmd.includes('100 63 -10'))fail('RECHECKED_DIRTY_ANCHOR');
    recoveryProbes++;
    return {statusCode:0,successCount:1};
  }},payload,payload,uncertain);
  if(recoveryProbes!==4)fail('RESUME_CLEAN_ANCHORS');
  report('test',{result:'PASS',commands:2,blocks:5,worldConnections:0,
    revisionTest:'PASS',partialRepairTest:'PASS'});
}
const main=process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href;
if(main){
  const [action,filename,...extra]=process.argv.slice(2);
  try{
    if(extra.length || (!['--self-test','--serve'].includes(action)&&!filename))fail('ARGS');
    if(action==='--self-test')await selfTest();
    else if(action==='--serve')await serve();
    else if(action==='--plan'){
      const compiled=plan(readPayload(filename));
      report('plan',{commands:compiled.commands.length,blocks:compiled.blocks,origin:compiled.origin});
    }else if(action==='--listen')await listen(readPayload(filename));
    else fail('ARGS');
  }catch(error){
    report('error',{message:String(error?.message??'unknown').slice(0,240)});
    process.exitCode=2;
  }
}
