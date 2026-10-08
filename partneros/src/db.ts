import pg from 'pg';
import {randomUUID} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {candidateInput,publicWebsite,score,transitions} from './domain.ts';
import type {Evidence,Recommendation} from './domain.ts';
// Small SQL interface also exercised against embedded Postgres in tests.
export interface SQL {query(text:string,values?:any[]):Promise<{rows:any[];rowCount?:number|null}>}
export const pool=()=>new pg.Pool({connectionString:process.env.DATABASE_URL,max:5});
export async function migrate(db:SQL){await db.query(await readFile(new URL('../migrations/001_initial.sql',import.meta.url),'utf8')); await db.query("INSERT INTO schema_migrations(version) VALUES('001') ON CONFLICT DO NOTHING");}
export class Repository {
 db:SQL;
 constructor(db:SQL){this.db=db;}
 async audit(id:string|null,actor:string,action:string,details:unknown={}){await this.db.query('INSERT INTO audit(candidate_id,actor,action,details) VALUES($1,$2,$3,$4)',[id,actor,action,JSON.stringify(details)]);}
 async create(input:unknown){const p=candidateInput.parse(input),web=publicWebsite(p.website),id=randomUUID();
 const result=await this.db.query(`INSERT INTO candidates(id,name,domain,website,segment,brief) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(domain) DO NOTHING RETURNING *`,[id,p.name,web.domain,web.website,p.segment,p.brief]);
 if(result.rows.length){await this.audit(id,'Colby','candidate_created');return {candidate:result.rows[0],duplicate:false};}
 return {candidate:(await this.db.query('SELECT * FROM candidates WHERE domain=$1',[web.domain])).rows[0],duplicate:true};}
 async get(id:string){return (await this.db.query('SELECT * FROM candidates WHERE id=$1',[id])).rows[0];}
 async list(){return (await this.db.query('SELECT * FROM candidates ORDER BY updated_at DESC LIMIT 500')).rows;}
 async enqueue(id:string,mode:string){
 const c=await this.get(id); if(!c||c.suppressed) throw new Error('Candidate is missing or suppressed.');
 const j=await this.db.query(`WITH created AS (INSERT INTO jobs(id,candidate_id,mode) SELECT $1,id,$3 FROM candidates WHERE id=$2 AND NOT suppressed AND NOT EXISTS(SELECT 1 FROM settings WHERE paused) ON CONFLICT DO NOTHING RETURNING *), changed AS (UPDATE candidates SET stage='researching',version=version+1,updated_at=now() WHERE id IN(SELECT candidate_id FROM created) AND NOT suppressed RETURNING id) SELECT * FROM created`,[randomUUID(),id,mode]);
 if(j.rows.length)await this.audit(id,'Colby','research_queued',{mode});
 return j.rows[0];}
 async claim(){
 await this.db.query("WITH failed AS (UPDATE jobs SET state='failed',error='Worker lease expired after maximum attempts',updated_at=now() WHERE state='running' AND lease_until<now() AND attempts>=3 RETURNING candidate_id) UPDATE candidates SET stage='blocked',version=version+1,updated_at=now() WHERE id IN(SELECT candidate_id FROM failed) AND stage='researching'");
 const token=randomUUID();
 return (await this.db.query(`UPDATE jobs SET state='running', attempts=attempts+1,lease_token=$1,lease_until=now()+interval '3 minutes',updated_at=now() WHERE id=(SELECT j.id FROM jobs j JOIN candidates c ON c.id=j.candidate_id WHERE NOT c.suppressed AND NOT EXISTS(SELECT 1 FROM settings WHERE paused) AND (j.state='queued' OR (j.state='running' AND j.lease_until<now() AND j.attempts<3)) ORDER BY j.created_at FOR UPDATE OF j SKIP LOCKED LIMIT 1) RETURNING *`,[token])).rows[0];}
 async checkpoint(job:any,evidence:Evidence){await this.db.query("UPDATE jobs SET checkpoint=$3,updated_at=now() WHERE id=$1 AND lease_token=$2 AND state='running'",[job.id,job.lease_token,JSON.stringify(evidence)]);}
 async reserve(job:any,amount:number,max:number){const r=await this.db.query("UPDATE jobs SET budget_reserved=budget_reserved+$3 WHERE id=$1 AND lease_token=$2 AND state='running' AND budget_reserved+$3<=$4 RETURNING id",[job.id,job.lease_token,amount,max]);if(!r.rows.length) throw new Error('Research budget exhausted; review the run before retrying.');}
 async complete(job:any,evidence:Evidence,recommendation:Recommendation,usage:unknown){
 // A single statement atomically inserts the assessment, completes the leased job and updates the candidate.
 const r=await this.db.query(`WITH current_job AS (SELECT j.* FROM jobs j JOIN candidates c ON c.id=j.candidate_id WHERE j.id=$1 AND j.lease_token=$2 AND j.state='running' AND NOT c.suppressed FOR UPDATE OF j,c), assessment AS (INSERT INTO assessments(id,candidate_id,job_id,mode,evidence,recommendation,score) SELECT $3,candidate_id,id,mode,$4,$5,$6 FROM current_job ON CONFLICT(job_id) DO NOTHING RETURNING candidate_id), finished AS (UPDATE jobs SET state='done',result=$5,usage=$7,lease_until=NULL,updated_at=now() WHERE id IN(SELECT id FROM current_job) RETURNING candidate_id) UPDATE candidates SET stage=CASE WHEN stage='researching' THEN 'review' ELSE stage END,version=version+1,updated_at=now() WHERE id IN(SELECT candidate_id FROM finished) RETURNING id`,[job.id,job.lease_token,randomUUID(),JSON.stringify(evidence),JSON.stringify(recommendation),JSON.stringify(score(recommendation)),JSON.stringify(usage)]);
 if(r.rows.length)await this.audit(job.candidate_id,'worker','research_completed',{job_id:job.id,mode:job.mode});}
 async fail(job:any,message:string){await this.db.query("WITH failed AS (UPDATE jobs SET state='failed',error=$3,lease_until=NULL,updated_at=now() WHERE id=$1 AND lease_token=$2 AND state='running' RETURNING candidate_id) UPDATE candidates SET stage='blocked',updated_at=now(),version=version+1 WHERE id IN(SELECT candidate_id FROM failed) AND stage='researching'",[job.id,job.lease_token,message]);}
 async notes(id:string,version:number,notes:string,relationship:boolean){const r=await this.db.query('UPDATE candidates SET notes=$3,existing_relationship=$4,version=version+1,updated_at=now() WHERE id=$1 AND version=$2 RETURNING id',[id,version,notes.slice(0,8000),relationship]);if(!r.rows.length)throw new Error('This candidate changed. Reload before saving.');await this.audit(id,'Colby','notes_updated');}
 async suppress(id:string){await this.db.query("UPDATE candidates SET suppressed=true,stage='blocked',version=version+1,updated_at=now() WHERE id=$1",[id]);await this.db.query("UPDATE jobs SET state='cancelled',updated_at=now() WHERE candidate_id=$1 AND state IN ('queued','running')",[id]);await this.audit(id,'Colby','suppressed');}
 async review(id:string,version:number,assessmentId:string,action:string,comment:string){if(!transitions[action])throw new Error('Invalid review');
 const r=await this.db.query(`WITH changed AS (UPDATE candidates SET stage=$4,version=version+1,updated_at=now() WHERE id=$1 AND version=$2 AND NOT suppressed AND stage IN ('review','accepted','rejected','nurture') AND $3::uuid=(SELECT id FROM assessments WHERE candidate_id=$1 ORDER BY created_at DESC LIMIT 1) RETURNING id) INSERT INTO reviews(id,candidate_id,assessment_id,action,comment,actor) SELECT $5,id,$3,$6,$7,'Colby' FROM changed RETURNING id`,[id,version,assessmentId,transitions[action],randomUUID(),action,comment.slice(0,4000)]);
 if(!r.rows.length)throw new Error('Review is stale or candidate is not reviewable. Reload the page.');await this.audit(id,'Colby','review_'+action);}
}
