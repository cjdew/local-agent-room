/* Optional real-browser smoke test. Requires Playwright, not used at runtime. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');
const {chromium} = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const state = fs.mkdtempSync(path.join(os.tmpdir(), 'room-ui-'));
  const python = process.env.PYTHON || 'python3';
  const args = [path.join(root, 'room.py'), '--data-dir', state];
  let server, browser;
  try {
    const add = spawnSync(python, [...args, 'add-agent', 'reviewer'], {encoding:'utf8'});
    assert.equal(add.status, 0);
    server = spawn(python, [...args, 'serve', '--port', '18765'], {stdio:['ignore','pipe','pipe']});
    const url = await new Promise((resolve,reject) => {
      let text = '';
      const timer = setTimeout(() => reject(new Error('Server startup timed out')), 10000);
      server.stdout.on('data', chunk => {
        text += chunk.toString();
        const match = text.match(/http:\/\/127\.0\.0\.1:\d+\/#token=[\w-]+/);
        if (match) { clearTimeout(timer); resolve(match[0]); }
      });
      server.once('exit', () => {clearTimeout(timer); reject(new Error('Server exited before startup'));});
    });
    browser = await chromium.launch({headless:true});
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(url);
    await page.getByRole('status').filter({hasText:'Connected locally'}).waitFor();
    assert.equal(new URL(page.url()).hash, '');
    await page.getByLabel('Your message').fill('Please review the change.');
    await page.getByRole('button', {name:'Send message'}).click();
    await page.getByText('Please review the change.', {exact:true}).waitFor();
    assert.equal(await page.getByLabel('Your message').inputValue(), '');

    const token = JSON.parse(fs.readFileSync(path.join(state,'credentials','reviewer.json'),'utf8')).token;
    const response = await fetch('http://127.0.0.1:18765/api/messages', {
      method:'POST', headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},
      body:JSON.stringify({body:'<img src=x onerror="window.roomXss=true"> Reviewed safely.',client_id:'ui-test',revision:'a'.repeat(40)})
    });
    assert.equal(response.status,200);
    await page.getByText('<img src=x onerror="window.roomXss=true"> Reviewed safely.',{exact:true}).waitFor();
    assert.equal(await page.locator('#messages img').count(),0);
    assert.equal(await page.evaluate(() => window.roomXss),undefined);
    await page.getByText('commit '+'a'.repeat(40),{exact:true}).waitFor();
    await page.getByRole('button',{name:'Enable wake'}).click();
    await page.getByRole('button',{name:'Stop all wake'}).waitFor();
    await page.getByRole('button',{name:'Stop all wake'}).click();
    await page.getByRole('button',{name:'Enable wake'}).waitFor();
    await page.getByRole('button',{name:'New room'}).click();
    await page.getByLabel('Room ID',{exact:true}).fill('design');
    await page.getByLabel('Title',{exact:true}).fill('Design review');
    await page.getByRole('button',{name:'Create room',exact:true}).click();
    await page.getByRole('heading',{name:'Design review',exact:true}).waitFor();
    await page.getByLabel('Your message').fill('A separate conversation.');
    await page.getByRole('button',{name:'Send message'}).click();
    await page.getByText('A separate conversation.',{exact:true}).waitFor();
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.equal(errors.length,0,errors.join('\n'));
    console.log('Browser smoke passed: login, posting, agent replies, plain-text safety, wake controls, rooms, and mobile width.');
  } finally {
    if (browser) await browser.close();
    if (server && server.exitCode === null) {
      await new Promise(resolve => {server.once('exit',resolve);server.kill('SIGINT');});
    }
    fs.rmSync(state,{recursive:true,force:true});
  }
})().catch(error => {console.error(error.message);process.exitCode=1;});
