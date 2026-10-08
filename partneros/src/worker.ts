import {pool,Repository} from './db.ts';
import {runJob} from './research.ts';
const db=pool(),repo=new Repository(db);let stopping=false;
process.on('SIGTERM',()=>{stopping=true;});process.on('SIGINT',()=>{stopping=true;});
console.log('PartnerOS worker ready.');
try{while(!stopping){const job=await repo.claim();if(job)await runJob(repo,job);else await new Promise(r=>setTimeout(r,1000));}}finally{await db.end();}
