import {createServer} from 'node:http';
import type {IncomingMessage} from 'node:http';
import {readFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {pool,Repository} from './db.ts';
import {newSession,session,verifyPassword} from './auth.ts';
import * as view from './views.ts';
import {integrationStatus,researchConfig} from './research.ts';
async function form(req:IncomingMessage){let body='';for await(const part of req){body+=part;if(Buffer.byteLength(body)>16000)throw new Error('Form too large');}return Object.fromEntries(new URLSearchParams(body));}
export function makeServer(repo:Repository,env=process.env){
 const secret=env.SESSION_SECRET||'',hash=env.REVIEWER_PASSWORD_HASH||'',origin=env.APP_ORIGIN||'';
 if(secret.length<32||!hash||!origin)throw new Error('Set SESSION_SECRET (32+ characters), REVIEWER_PASSWORD_HASH and APP_ORIGIN.');
 if(env.NODE_ENV==='production'&&!origin.startsWith('https://'))throw new Error('Production requires HTTPS APP_ORIGIN');
 let failures=0,windowStart=Date.now();
 return createServer(async(req,res)=>{
 res.setHeader('Content-Security-Policy',"default-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','same-origin');res.setHeader('Cache-Control','no-store');
 const url=new URL(req.url||'/',origin),path=url.pathname;
 const send=(body:string,status=200)=>{res.writeHead(status,{'Content-Type':'text/html; charset=utf-8'});res.end(body);};
 const redirect=(dest:string)=>{res.writeHead(303,{Location:dest});res.end();};
 try{
 if(path==='/healthz'){await repo.db.query('SELECT 1');res.writeHead(200,{'Content-Type':'application/json'});res.end('{"status":"ready"}');return;}
 if(path==='/style.css'){res.writeHead(200,{'Content-Type':'text/css'});res.end(await readFile(new URL('../public/style.css',import.meta.url)));return;}
 const s=session(req.headers.cookie,secret);
 if(req.method==='POST'&&req.headers.origin!==origin){send('Request origin rejected',403);return;}
 if(path==='/login'&&req.method==='POST'){
  if(Date.now()-windowStart>60000){failures=0;windowStart=Date.now();}
  if(failures>=10){send('Too many sign-in attempts. Try again in one minute.',429);return;}
  const data=await form(req);failures++;
  if(!verifyPassword(data.password||'',hash)){send(view.login(),401);return;}
  failures=0;res.setHeader('Set-Cookie',`partneros=${newSession(secret)}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800${origin.startsWith('https:')?'; Secure':''}`);redirect('/');return;
 }
 if(!s){send(view.login(),path==='/'||path==='/login'?200:401);return;}
 const csrf=s.csrf;
 if(req.method==='POST'){
  const data=await form(req);if(data.csrf!==csrf){send('Form expired. Reload and try again.',403);return;}
  if(path==='/logout'){res.setHeader('Set-Cookie','partneros=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0');redirect('/');return;}
  if(path==='/pause'){await repo.db.query('UPDATE settings SET paused=$1 WHERE id=1',[data.paused==='true']);await repo.audit(null,'Colby','pause_changed',{paused:data.paused==='true'});redirect('/settings');return;}
  if(path==='/candidates'){const result=await repo.create(data);redirect('/candidates/'+result.candidate.id+(result.duplicate?'?notice=Existing+company+found.+No+duplicate+created.':''));return;}
  const match=path.match(/^\/candidates\/([0-9a-f-]{36})\/(research|review|notes|suppress)$/);
  if(match){const [,id,action]=match;
   if(action==='research'){
    if(data.mode!=='live'&&data.mode!=='fixture')throw new Error('Invalid research mode');
    if(data.mode==='fixture'&&env.DEMO_MODE!=='true')throw new Error('Demo mode is disabled.');
    if(data.mode==='live')researchConfig(env);
    const job=await repo.enqueue(id,data.mode);if(!job)throw new Error('Research is already queued or processing is paused.');
   }
   if(action==='notes')await repo.notes(id,Number(data.version),data.notes||'',data.relationship==='on');
   if(action==='suppress')await repo.suppress(id);
   if(action==='review')await repo.review(id,Number(data.version),data.assessment,data.action,data.comment||'');
   redirect('/candidates/'+id);return;
  }
  send('Action not available',404);return;
 }
 if(req.method!=='GET'){send('Method not allowed',405);return;}
 const paused=(await repo.db.query('SELECT paused FROM settings WHERE id=1')).rows[0]?.paused??true;
 if(path==='/'){send(view.overview(await repo.list(),csrf,paused,url.searchParams.get('stage')||'',url.searchParams.get('q')||'',url.searchParams.get('notice')||''));return;}
 if(path==='/settings'){send(view.settings(csrf,paused,integrationStatus(env),env.DEMO_MODE==='true'));return;}
 const match=path.match(/^\/candidates\/([0-9a-f-]{36})$/);
 if(match){const id=match[1],c=await repo.get(id);if(!c){send('Candidate not found',404);return;}
  const [a,j,r,h]=await Promise.all([repo.db.query('SELECT * FROM assessments WHERE candidate_id=$1 ORDER BY created_at DESC LIMIT 1',[id]),repo.db.query('SELECT * FROM jobs WHERE candidate_id=$1 ORDER BY created_at DESC LIMIT 30',[id]),repo.db.query('SELECT * FROM reviews WHERE candidate_id=$1 ORDER BY created_at DESC',[id]),repo.db.query('SELECT * FROM audit WHERE candidate_id=$1 ORDER BY created_at DESC LIMIT 50',[id])]);
  send(view.detail(c,a.rows[0],j.rows,r.rows,h.rows,csrf,env.DEMO_MODE==='true',url.searchParams.get('notice')||''));return;
 }
 send('Page not found',404);
 }catch(error){
 // Database and provider payloads may contain private data; expose only controlled validation messages.
 const known=error instanceof Error&&!('code' in error)&&error.name!=='ZodError';
 const message=known?error.message:'Unable to complete this operation. Check inputs or database configuration.';
 send(view.layout('Needs attention',`<section class="card"><h1>Needs attention</h1><p>${view.esc(message)}</p><a href="/">Return to pipeline</a></section>`),400);
 }
 });
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){const db=pool();const server=makeServer(new Repository(db));server.listen(Number(process.env.PORT||3000),'0.0.0.0',()=>console.log('PartnerOS web ready.'));process.on('SIGTERM',()=>server.close(()=>{void db.end();}));}
