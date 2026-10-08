import {test} from 'node:test';
import assert from 'node:assert/strict';
import {PGlite} from '@electric-sql/pglite';
import {Repository,migrate} from '../src/db.ts';
import {publicWebsite,score,validateRecommendation,allowedEvidenceUrl} from '../src/domain.ts';
import {fixture,runJob,ResearchError} from '../src/research.ts';
import {passwordHash,verifyPassword,newSession,session} from '../src/auth.ts';
import {makeServer} from '../src/server.ts';
async function setup(){const pg=new PGlite();const db={query:async(text:string,params?:any[])=>{if(text.includes('CREATE TABLE')){await pg.exec(text);return {rows:[]};}return await pg.query(text,params);}};await migrate(db);return {pg,repo:new Repository(db)};}
const candidate={name:'Example Inference',website:'https://www.example.com/path',segment:'Inference tools',brief:'Assess potential for a developer workflow integration.'};
test('public website normalization and private address rejection',()=>{
 assert.equal(publicWebsite(candidate.website).domain,'example.com');
 for(const url of ['http://example.com','https://127.0.0.1','https://[::1]','https://169.254.169.254/latest','https://localhost','https://a.internal','https://user:pw@example.com','https://example.com:444'])assert.throws(()=>publicWebsite(url));
 assert.equal(allowedEvidenceUrl('https://evil.com',['example.com']),false);
 assert.equal(allowedEvidenceUrl('https://docs.example.com/page',['example.com']),true);
});
test('unknown scores stay unknown and fabricated evidence IDs rejected',()=>{
 const f=fixture();assert.equal(score(f.recommendation).coverage,25);assert.equal(score(f.recommendation).fit,75);
 const r=structuredClone(f.recommendation);r.dimensions.forEach(d=>d.rating=null);assert.equal(score(r).fit,null);
 r.summary.evidence_ids=['FAKE'];assert.throws(()=>validateRecommendation(r,f.evidence));
});
test('full demo: duplicate detection, durable queue, review, human edits, stale approval',async()=>{
 const {pg,repo}=await setup();try{
  const first=await repo.create(candidate),id=first.candidate.id;
  const second=await repo.create({...candidate,website:'example.com'});assert.equal(second.duplicate,true);assert.equal(second.candidate.id,id);
  const queued=await repo.enqueue(id,'fixture');assert.ok(queued);assert.equal(await repo.enqueue(id,'fixture'),undefined);
  const job=await repo.claim();assert.equal(job.id,queued.id);assert.equal(await repo.claim(),undefined);
  const current=await repo.get(id);await repo.notes(id,current.version,'Keep this human note',true);
  const f=fixture();await repo.complete(job,f.evidence,f.recommendation,{fixture:true});
  await repo.complete(job,f.evidence,f.recommendation,{fixture:true});
  const done=await repo.get(id);assert.equal(done.notes,'Keep this human note');assert.equal(done.existing_relationship,true);assert.equal(done.stage,'review');
  const assessments=(await repo.db.query('SELECT * FROM assessments')).rows;assert.equal(assessments.length,1);
  await repo.review(id,done.version,assessments[0].id,'approve','Worth exploring, no outreach authorized.');
  assert.equal((await repo.get(id)).stage,'accepted');
  await assert.rejects(()=>repo.review(id,done.version,assessments[0].id,'reject','Stale'));
  assert.equal((await repo.db.query('SELECT * FROM reviews')).rows.length,1);
 }finally{await pg.close();}
});
test('expired lease resumes checkpoint and stale worker cannot publish',async()=>{
 const {pg,repo}=await setup();try{const {candidate:c}=await repo.create(candidate);await repo.enqueue(c.id,'live');const old=await repo.claim();const f=fixture();await repo.checkpoint(old,f.evidence);
 await repo.db.query("UPDATE jobs SET lease_until=now()-interval '1 minute'");const resumed=await repo.claim();assert.notEqual(old.lease_token,resumed.lease_token);assert.equal(resumed.checkpoint[0].id,'E1');
 await repo.complete(old,f.evidence,f.recommendation,{});assert.equal((await repo.db.query('SELECT * FROM assessments')).rows.length,0);
 await repo.complete(resumed,f.evidence,f.recommendation,{});assert.equal((await repo.db.query('SELECT * FROM assessments')).rows.length,1);
 }finally{await pg.close();}
});
test('suppression cancels work and blocks new runs; pause prevents claim',async()=>{
 const {pg,repo}=await setup();try{const {candidate:c}=await repo.create(candidate);await repo.enqueue(c.id,'fixture');
 await repo.db.query('UPDATE settings SET paused=true');assert.equal(await repo.claim(),undefined);await repo.db.query('UPDATE settings SET paused=false');
 const job=await repo.claim();await repo.suppress(c.id);const f=fixture();await repo.complete(job,f.evidence,f.recommendation,{});
 assert.equal((await repo.db.query('SELECT * FROM assessments')).rows.length,0);await assert.rejects(()=>repo.enqueue(c.id,'fixture'));assert.equal((await repo.get(c.id)).stage,'blocked');
 }finally{await pg.close();}
});
test('budget reservation survives retries and refuses over-budget calls',async()=>{const {pg,repo}=await setup();try{
 const {candidate:c}=await repo.create(candidate);await repo.enqueue(c.id,'live');const job=await repo.claim();await repo.reserve(job,.6,1);await assert.rejects(()=>repo.reserve(job,.6,1));
 }finally{await pg.close();}});
test('provider failure is retained as blocked and does not create an assessment',async()=>{const {pg,repo}=await setup();const old={...process.env};try{
 Object.assign(process.env,{ANTHROPIC_API_KEY:'test',ANTHROPIC_MODEL:'test',TAVILY_API_KEY:'test',APPROVED_RESEARCH_DOMAINS:'example.com',MODEL_INPUT_USD_PER_MILLION:'1',MODEL_OUTPUT_USD_PER_MILLION:'1',SEARCH_USD_PER_REQUEST:'.01',MAX_RUN_USD:'1'});
 const {candidate:c}=await repo.create(candidate);await repo.enqueue(c.id,'live');const job=await repo.claim();await runJob(repo,job,{search:async()=>{throw new ResearchError('Credentials rejected');},analyze:async()=>{throw new Error('must not run');}});
 assert.equal((await repo.get(c.id)).stage,'blocked');assert.equal((await repo.db.query('SELECT * FROM jobs')).rows[0].error,'Credentials rejected');assert.equal((await repo.db.query('SELECT * FROM assessments')).rows.length,0);
 }finally{process.env=old;await pg.close();}});
test('passwords, signed sessions and expiration',()=>{const hash=passwordHash('test password');assert.ok(verifyPassword('test password',hash));assert.ok(!verifyPassword('wrong',hash));const key='a'.repeat(32),s=newSession(key);assert.ok(session('partneros='+s,key));assert.equal(session('partneros='+s+'x',key),null);});
test('HTTP auth, CSRF, XSS escaping, and external actions unavailable',async()=>{
 const {pg,repo}=await setup();const origin='http://localhost';const secret='test-secret-'.repeat(4);const server=makeServer(repo,{APP_ORIGIN:origin,SESSION_SECRET:secret,REVIEWER_PASSWORD_HASH:passwordHash('test'),DEMO_MODE:'true'});
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));const addr=server.address() as {port:number},base=`http://127.0.0.1:${addr.port}`;
 try{
 assert.equal((await fetch(base+'/settings')).status,401);
 const login=await fetch(base+'/login',{method:'POST',headers:{Origin:origin,'Content-Type':'application/x-www-form-urlencoded'},body:'password=test',redirect:'manual'});assert.equal(login.status,303);
 const cookie=login.headers.get('set-cookie')!.split(';')[0],csrf=session(cookie,secret)!.csrf;
 assert.equal((await fetch(base+'/pause',{method:'POST',headers:{Cookie:cookie,Origin:origin},body:'csrf=wrong'})).status,403);
 assert.equal((await fetch(base+'/pause',{method:'POST',headers:{Cookie:cookie,Origin:'https://evil.com'},body:'csrf='+csrf})).status,403);
 const {candidate:c}=await repo.create({...candidate,name:'<script>alert(1)</script>'});const page=await(await fetch(base+'/candidates/'+c.id,{headers:{Cookie:cookie}})).text();assert.ok(page.includes('&lt;script&gt;'));assert.ok(!page.includes('<script>alert'));
 for(const path of ['/send','/hubspot/promote','/handoff/accept'])assert.equal((await fetch(base+path,{method:'POST',headers:{Cookie:cookie,Origin:origin},body:'csrf='+csrf})).status,404);
 }finally{await new Promise<void>(r=>server.close(()=>r()));await pg.close();}
});
test('database survives close/reopen with queued work intact',async()=>{
 const {mkdtemp,rm}=await import('node:fs/promises');const {tmpdir}=await import('node:os');const dir=await mkdtemp(tmpdir()+'/partneros-db-');
 let pg=new PGlite(dir);const db={query:async(t:string,p?:any[])=>{if(t.includes('CREATE TABLE')){await pg.exec(t);return {rows:[]};}return pg.query(t,p);}};
 try{await migrate(db);const repo=new Repository(db);const {candidate:c}=await repo.create(candidate);const queued=await repo.enqueue(c.id,'fixture');await pg.close();pg=new PGlite(dir);const resumed=await repo.claim();assert.equal(resumed.id,queued.id);const f=fixture();await repo.complete(resumed,f.evidence,f.recommendation,{});assert.equal((await repo.get(c.id)).stage,'review');}finally{await pg.close();await rm(dir,{recursive:true,force:true});}
});
