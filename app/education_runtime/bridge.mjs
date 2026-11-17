/**
 * MAB Studio Education read-only transport, restricted B134-style probe.
 * Uses the same SocketBE 2.8.0 interface exercised in MAB B134; this file
 * intentionally has no commands capable of changing Minecraft world state.
 * No world auto-sync, no SOURCE construction, no remotely-controlled commands.
 */
import { pathToFileURL } from 'node:url';

export const PORT = 8000;
export const READ_TOKEN = '!mab-read';
export const SCHEMA = 'minecraftaibuilder-b134-live-readonly-diagnostic-v1';
const MAX_ABS_XZ = 30000000;
const COUNTABLE = new Set([0,1]);

const fail = (text) => { throw new Error('STUDIO_EDUCATION_' + text); };

export function coordinates(words) {
  if (!Array.isArray(words) || words.length !== 3 ||
      !words.every(x => typeof x === 'string' && /^-?(0|[1-9][0-9]*)$/.test(x)))
    fail('INVALID_XYZ');
  const [x,y,z] = words.map(Number);
  if (![x,y,z].every(Number.isSafeInteger) ||
      Math.abs(x)>MAX_ABS_XZ || Math.abs(z)>MAX_ABS_XZ || y< -64 || y>512)
    fail('XYZ_OUT_OF_RANGE');
  return {x,y,z};
}
export function readCommands(pos) {
  const {x,y,z} = coordinates([String(pos?.x),String(pos?.y),String(pos?.z)]);
  const baseline=`testforblock ${x} ${y} ${z} minecraft:oak_stairs`;
  return [baseline, ...[0,1,2,3].map(value =>
    baseline + ` ["weirdo_direction" = ${value}]`)];
}
export function classify(result) {
  if (!result || result.transportError === true ||
      !Number.isInteger(result.statusCode) ||
      result.statusCode<0 || !COUNTABLE.has(result.successCount))return 'UNKNOWN';
  return result.successCount===1 ? 'MATCH' : 'NO_MATCH';
}
function minimizedResponse(res) {
  const r=res && typeof res === 'object' ? res : {};
  return {
    statusCode: typeof r.statusCode==='number' && Number.isFinite(r.statusCode) ? r.statusCode : null,
    successCount: COUNTABLE.has(r.successCount) ? r.successCount : null,
    statusMessage: typeof r.statusMessage==='string' ? r.statusMessage.slice(0,512) : null,
    transportError: r.transportError === true
  };
}
export function evaluate(pos, results) {
  const commands=readCommands(pos);
  if (!Array.isArray(results) || results.length!==5)fail('EXPECTED_FIVE_RESULTS');
  const rows=commands.map((command, i)=>({
    command,classification:classify(results[i]),response:minimizedResponse(results[i])
  }));
  const hits=rows.slice(1).flatMap((r,i)=>r.classification==='MATCH'?[i]:[]);
  const good=rows[0].classification==='MATCH' && hits.length===1 &&
    rows.slice(1).filter(r=>r.classification==='NO_MATCH').length===3;
  return {
    schema:SCHEMA,coords:pos,results:rows,
    provisionalStateValue:good?hits[0]:null,
    outcome:good?'UNIQUE_FOUR_STATE_RESPONSE_NEEDS_VISUAL_REVIEW':
                 'INCONCLUSIVE_NEEDS_GAME_INSPECTION',
    transportReported:true,independentImageReview:false,walkabilityVerified:false,
    authenticatedStairCertification:false,constructionAllowed:false,
    readiness:'NOT_PREPARED',operations:[],
    source:'MAB Studio opt-in loopback probe, reusing B134 five-command diagnostic contract'
  };
}
export async function queryReadOnly(world,pos) {
  if (!world || typeof world.runCommand !== 'function')fail('INVALID_WORLD');
  const commands=readCommands(pos), results=[];
  for (const command of commands) {
    // Defense in depth: only B134's five exact generated commands.
    if (!command.startsWith('testforblock ') || !commands.includes(command) || command.includes('\n'))
      fail('COMMAND_NOT_ALLOWLISTED');
    try {
      results.push(await world.runCommand(command,{timeout:8000,noResponse:false}));
    } catch(error) {
      results.push({transportError:true,
        statusMessage:String(error?.message??'unknown').slice(0,512)});
    }
  }
  return evaluate(pos,results);
}
function emit(type,extra={}) {
  process.stdout.write(JSON.stringify({type,...extra}) + '\n');
}
function leave(code){setTimeout(()=>process.exit(code),125).unref();}

async function selfTest(){
  const pos=coordinates(['123','-60','-18']);
  const used=[];
  const world={runCommand:async command=>{
    used.push(command);
    return {statusCode:0,successCount:used.length===1 || used.length===5?1:0};
  }};
  const report=await queryReadOnly(world,pos);
  if (used.length!==5 || !used.every(x=>x.startsWith('testforblock ')) ||
      report.provisionalStateValue!==3 || report.constructionAllowed!==false ||
      report.operations.length!==0)fail('SELF_TEST_FAILED');
  emit('test',{result:'PASS',commands:used.length,worldWrites:0});
}

async function listen(args) {
  const pos=coordinates(args);
  let api;
  try { api=await import('socket-be'); }
  catch (error) { fail('SOCKET_BE_MISSING_RUN_NPM_INSTALL_FIRST'); }
  const {Server,ServerEvent}=api;
  if (typeof Server!=='function' || !ServerEvent)fail('SOCKET_BE_INTERFACE');
  const server=new Server({port:PORT,webSocketOptions:{host:'127.0.0.1',maxPayload:65536}});
  let started=false;
  const timeLimit=setTimeout(()=>{
    if (started)return;
    emit('timeout',{message:'NO_AUTHENTICATED_GAME_READBACK',worldWrites:0});
    leave(3);
  },150000);
  server.on(ServerEvent.Open,()=>emit('listening',{
    host:'127.0.0.1',port:PORT,
    message:'Minecraft Educationで /wsserver 127.0.0.1:8000 の後 !mab-read'
  }));
  server.on(ServerEvent.PlayerChat,async ev=>{
    if (started || ev?.message!==READ_TOKEN || !ev?.world)return;
    started=true;
    clearTimeout(timeLimit);
    try {
      const result=await queryReadOnly(ev.world,pos);
      emit('result',{report:result,worldWrites:0});
      leave(0);
    }catch(error){
      emit('error',{message:String(error?.message??'UNKNOWN').slice(0,260),worldWrites:0});
      leave(2);
    }
  });
}

const main=process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href;
if(main) {
  const [action,...args]=process.argv.slice(2);
  try {
    if(action==='--self-test')await selfTest();
    else if(action==='--listen')await listen(args);
    else fail('ONLY_LISTEN_OR_SELF_TEST');
  }catch(error) {
    emit('error',{message:String(error?.message??'UNKNOWN').slice(0,260),worldWrites:0});
    process.exitCode=2;
  }
}
