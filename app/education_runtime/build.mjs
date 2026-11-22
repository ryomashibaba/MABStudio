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
// Studio currently has no verified source for formal ART adoption. Keep every
// world-write route closed until an external, trusted approval source exists.
const FORMAL_ART_ADOPTION_VERIFIED=false;
export const MATERIALS=Object.freeze([
  'minecraft:stone','minecraft:white_concrete','minecraft:iron_block',
  'minecraft:glass','minecraft:oak_planks','minecraft:glowstone',
  'minecraft:orange_concrete'
]);
const LAYERS=Object.freeze(['B143_STONE','B143_CONCRETE','B143_METAL',
  'B143_GLASS','B143_WOOD','B143_GLOW','B143_ACCENT']);
const MAX_VOXELS=160000;
const MAX_FILL=32768;
const MAX_COMMANDS=MAX_VOXELS;
const fail=(reason)=>{throw new Error('EDUCATION_BUILD_'+reason);};
function requireFormalArtAdoption(){
  if(!FORMAL_ART_ADOPTION_VERIFIED)fail('FORMAL_ART_ADOPTION_REQUIRED');
}
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
export function plan(p) {
  validatePayload(p);
  const rows=new Map();
  for(const [x,y,z,m] of p.primitives){
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
      const block=MATERIALS[s.m];
      const command=n===1 ? `setblock ${a.join(' ')} ${block}` :
        `fill ${a.join(' ')} ${b.join(' ')} ${block}`;
      commands.push({command,blocks:n});
      blocks+=n;
      if(commands.length>MAX_COMMANDS)fail('COMMAND_BUDGET');
    }
  }
  if(blocks!==p.voxels)fail('CELL_ACCOUNTING');
  return {commands,blocks,origin,sourceSha256:p.sourceSha256,
    geometrySha256:p.geometrySha256};
}

function report(type,body={}){process.stdout.write(JSON.stringify({type,...body})+'\n');}
function exitSoon(code){setTimeout(()=>process.exit(code),75).unref();}
function readPayload(file){
  if(typeof file!=='string' || !file || !fs.statSync(file).isFile() ||
      fs.statSync(file).size>9*1024*1024)fail('PAYLOAD_FILE');
  return JSON.parse(fs.readFileSync(file,'utf8'));
}
export function commandSucceeded(result){
  // SocketBE CommandResult supplies statusCode, not a guaranteed successCount.
  // The same nonnegative status contract is used by its World.fillBlocks API.
  return result && result.transportError!==true &&
    Number.isInteger(result.statusCode) && result.statusCode>=0;
}
export async function perform(world,compiled,onProgress=()=>{}){
  requireFormalArtAdoption();
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
  requireFormalArtAdoption();
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
  requireFormalArtAdoption();
  const {Server,ServerEvent}=await import('socket-be');
  const {createInterface}=await import('node:readline');
  const server=new Server({port:PORT,webSocketOptions:{host:'127.0.0.1',maxPayload:65536}});
  let busy=false;
  server.on(ServerEvent.Open,()=>report('listening',{port:PORT,
    connectCommand:`/wsserver 127.0.0.1:${PORT}`}));
  const reader=createInterface({input:process.stdin,crlfDelay:Infinity});
  reader.on('line',async line=>{
    if(line.length>9*1024*1024){report('error',{message:'PAYLOAD_TOO_LARGE'});return;}
    if(busy){report('error',{message:'BUILD_ALREADY_RUNNING'});return;}
    busy=true;
    let compiled;
    try {
      const request=JSON.parse(line);
      if(request?.action!=='build')fail('UNKNOWN_ACTION');
      const worlds=server.getWorlds();
      if(worlds.length!==1){
        report('error',{message:worlds.length===0 ?
          'MINECRAFT_NOT_CONNECTED: /wsserver 127.0.0.1:8001' :
          'MULTIPLE_WORLDS_CONNECTED',completed:0,confirmedBlocks:0});
        return;
      }
      let payload=request.payload;
      if(payload?.placementMode==='player'){
        const player=await worlds[0].getLocalPlayer();
        const position=await player.getLocation();
        if(!position || !['x','y','z'].every(a=>Number.isFinite(position[a])))fail('PLAYER_POSITION_UNAVAILABLE');
        payload={...payload,origin:{x:Math.floor(position.x)+8,
          y:Math.floor(position.y)-1,z:Math.floor(position.z)+8}};
      }
      compiled=plan(payload);
      report('started',{commands:compiled.commands.length,blocks:compiled.blocks,origin:compiled.origin});
      const result=await perform(worlds[0],compiled,p=>report('progress',p));
      report('result',{...result,sourceSha256:compiled.sourceSha256,
        geometrySha256:compiled.geometrySha256,origin:compiled.origin,readbackVerified:false});
    }catch(error){
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
  if(compiled.blocks!==5||compiled.commands.length!==2||FORMAL_ART_ADOPTION_VERIFIED)
    fail('SELF_TEST');
  report('test',{result:'PASS',commands:2,blocks:5,worldWrites:0,
    formalArtAdoptionVerified:FORMAL_ART_ADOPTION_VERIFIED});
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
