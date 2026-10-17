const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {gzipSync} = require('node:zlib');
const {webcrypto} = require('node:crypto');
const {runInNewContext} = require('node:vm');
const source = readFileSync('public/dashboard/downloads.js','utf8');
async function scenario(mode) {
  const raw = Buffer.from('code,value\r\n1000,1.2345678901234567\r\n');
  const packed = gzipSync(raw);
  const hash = async bytes => Buffer.from(await webcrypto.subtle.digest('SHA-256',bytes)).toString('hex');
  const link = {href:'https://example.test/validation/controls.csv.gz', dataset:{originalName:'controls.csv', originalBytes:String(raw.length), originalSha256:await hash(raw), packedSha256:await hash(packed)},setAttribute(){},removeAttribute(){}};
  if (mode==='bad-original') link.dataset.originalSha256='0'.repeat(64);
  if (mode==='bad-packed') link.dataset.packedSha256='0'.repeat(64);
  let handler, saved, prevented=false, clicked=false; const alerts=[];
  const context={DecompressionStream: mode==='fallback'?undefined:DecompressionStream,crypto:webcrypto,Blob,Response,Uint8Array,Number,Array,Error,setTimeout:()=>{},alert:x=>alerts.push(x),URL:{createObjectURL:blob=>{saved=blob;return 'blob:test';},revokeObjectURL(){}},fetch:async()=>new Response(packed),document:{addEventListener:(name,fn)=>handler=fn,createElement:()=>({click:()=>clicked=true})}};
  runInNewContext(source,context);
  await handler({target:{closest:()=>link},preventDefault:()=>prevented=true});
  if(mode==='ok') {
    assert.equal(prevented,true);assert.equal(clicked,true);assert.equal(alerts.length,0);
    assert.deepEqual(Buffer.from(await saved.arrayBuffer()),raw);
  } else if(mode==='fallback') {
    assert.equal(prevented,false);assert.equal(link.download,'controls.csv.gz');assert.equal(alerts.length,1);
  } else {
    assert.equal(prevented,true);assert.equal(clicked,false);assert.equal(saved,undefined);assert.equal(alerts.length,1);
  }
}
(async()=>{for(const mode of ['ok','bad-original','bad-packed','fallback']) await scenario(mode);console.log('4 download transport scenarios passed');})().catch(e=>{console.error(e);process.exit(1);});
