// Local UI check only; isolated invented data, no external services.
import {PGlite} from '@electric-sql/pglite';
import {Repository,migrate} from '../src/db.ts';
import {makeServer} from '../src/server.ts';
import {passwordHash} from '../src/auth.ts';
import {fixture} from '../src/research.ts';
const pg=new PGlite();const db={query:async(t:string,p?:any[])=>{if(t.includes('CREATE TABLE')){await pg.exec(t);return {rows:[]};}return pg.query(t,p);}};await migrate(db);const repo=new Repository(db);
const c=(await repo.create({name:'Example Inference · Demo',website:'example.com',segment:'Developer tools',brief:'Explore a potential workflow integration for image inference developers. This is invented demonstration data.'})).candidate;
await repo.enqueue(c.id,'fixture');const job=await repo.claim(),f=fixture();await repo.complete(job,f.evidence,f.recommendation,{fixture:true,estimated_usd:0});
const server=makeServer(repo,{APP_ORIGIN:'http://127.0.0.1:4318',SESSION_SECRET:'temporary-visual-check-secret-32-characters',REVIEWER_PASSWORD_HASH:passwordHash('local-demo-only'),DEMO_MODE:'true'});
await new Promise<void>(r=>server.listen(4318,'127.0.0.1',r));
try{
 const path=process.env.PLAYWRIGHT_MODULE!;const {chromium}=await import(path);const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{const page=await browser.newPage({viewport:{width:1440,height:1050}});await page.goto('http://127.0.0.1:4318');await page.locator('input[name=password]').fill('local-demo-only');await page.getByRole('button',{name:'Sign in',exact:true}).click();if(page.url()!=='http://127.0.0.1:4318/'){console.log('Login result',page.url(),await page.locator('body').innerText());throw new Error('Login did not redirect');}await page.screenshot({path:'artifacts/pipeline.png',fullPage:true});await page.goto('http://127.0.0.1:4318/candidates/'+c.id);await page.screenshot({path:'artifacts/candidate.png',fullPage:true});await page.setViewportSize({width:390,height:844});await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));await page.screenshot({path:'artifacts/mobile.png'});const width=await page.evaluate(()=>document.documentElement.scrollWidth);if(width>390)throw new Error('Mobile horizontal overflow: '+width);await page.locator('textarea[name=comment]').fill('Demo research accepted for further internal review.');await page.getByRole('button',{name:'Accept research',exact:true}).click();if(!(await page.locator('header').innerText()).toLowerCase().includes('accepted'))throw new Error('Browser review failed');await page.getByRole('heading',{name:'Your review',exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:'artifacts/mobile-review.png'});console.log('UI screenshots and browser review flow verified.');}finally{await browser.close();}
}finally{await new Promise<void>(r=>server.close(()=>r()));await pg.close();}
