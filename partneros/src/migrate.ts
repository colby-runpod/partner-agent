import {pool,migrate} from './db.ts';
const db=pool(),client=await db.connect();try{await client.query('BEGIN');await client.query('SELECT pg_advisory_xact_lock(812739)');await migrate(client);await client.query('COMMIT');console.log('PartnerOS schema ready.');}catch(e){await client.query('ROLLBACK');throw e;}finally{client.release();await db.end();}
