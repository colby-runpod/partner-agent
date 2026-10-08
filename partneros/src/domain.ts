import {isIP} from 'node:net';
import {z} from 'zod';
export const weights=[25,25,20,15,15];
export const criteria=['Strategic / use-case fit','Customer / distribution potential','Technical plausibility','Commercial potential','Timing / stakeholders'];
export function publicWebsite(input:string){
 const u=new URL(input.includes('://')?input:'https://'+input);
 const host=u.hostname.toLowerCase().replace(/^www\./,'').replace(/\.$/,'');
 if(u.protocol!=='https:'||u.username||u.password||u.port||isIP(host)||!host.includes('.')||!/^([a-z0-9-]+\.)+[a-z]{2,}$/.test(host)||/(^|\.)(localhost|local|internal|test|invalid|example|onion)$/.test(host)||host==='metadata.google.internal') throw new Error('Use a public HTTPS company website, without credentials or a port.');
 return {domain:host,website:'https://'+host};
}
export const candidateInput=z.object({name:z.string().trim().min(2).max(160),website:z.string().max(500),segment:z.string().trim().min(1).max(100),brief:z.string().trim().min(10).max(4000)});
export const evidenceSchema=z.array(z.object({id:z.string(),url:z.string(),title:z.string(),excerpt:z.string(),retrieved_at:z.string()})).max(5);
const claim=z.object({text:z.string().min(1).max(1200),evidence_ids:z.array(z.string()).min(1).max(5)}).strict();
export const recommendationSchema=z.object({
 summary:claim, thesis:claim,
 claims:z.array(claim).max(8),
 dimensions:z.array(z.object({rating:z.number().int().min(0).max(4).nullable(),reason:z.string().max(600),evidence_ids:z.array(z.string()).max(5)}).strict()).length(5),
 confidence:z.enum(['low','medium','high']),unknowns:z.array(z.string().max(500)).min(1).max(12),
 next_action:z.string().max(1000),outreach_draft:z.string().max(2500)
}).strict();
export type Evidence=z.infer<typeof evidenceSchema>;
export type Recommendation=z.infer<typeof recommendationSchema>;
export function validateRecommendation(value:unknown,evidence:Evidence){
 const r=recommendationSchema.parse(value);const ids=new Set(evidence.map(e=>e.id));
 for(const c of [r.summary,r.thesis,...r.claims,...r.dimensions]){
  if(c.evidence_ids.some(id=>!ids.has(id))) throw new Error('Unrecognized evidence reference');
  if('rating' in c&&c.rating!==null&&!c.evidence_ids.length) throw new Error('Known ratings require evidence');
 }
 return r;
}
export function score(r:Recommendation){
 const coverage=r.dimensions.reduce((s,d,i)=>s+(d.rating===null?0:weights[i]),0);
 const earned=r.dimensions.reduce((s,d,i)=>s+(d.rating===null?0:d.rating/4*weights[i]),0);
 return {coverage,fit:coverage?Math.round(earned/coverage*100):null,qualification:'Not conversation-validated'};
}
export function allowedEvidenceUrl(url:string,domains:string[]){
 try{const {domain}=publicWebsite(url); return domains.some(d=>domain===d||domain.endsWith('.'+d));}catch{return false;}
}
export const transitions:Record<string,string>={approve:'accepted',reject:'rejected',nurture:'nurture',more_research:'new'};
