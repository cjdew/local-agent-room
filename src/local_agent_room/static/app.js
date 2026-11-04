'use strict';
const $ = id => document.getElementById(id);
let token = sessionStorage.getItem('room-token') || '';
const fragment = new URLSearchParams(location.hash.slice(1));
const initialRoom = fragment.get('room') || 'general';
if (fragment.has('token')) {
  token = fragment.get('token');
  sessionStorage.setItem('room-token', token);
  history.replaceState(null, '', location.pathname);
}
let room = 'general', cursor = 0, generation = 0, polling = false, loading = false, enabled = false, retry = null;
let isAgent = false, canPost = true, sending = false, agentRound, roundState = null;
const seen = new Set();
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
function el(tag, text, className) { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; }
async function api(path, body) {
  const response = await fetch('/api/' + path, {method: body === undefined ? 'GET' : 'POST',
    headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000)});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && !$('login').open) $('login').showModal();
    throw new Error(data.error || 'Request failed.');
  }
  return data;
}
function renderMessages(messages, reset = false) {
  if (reset) { $('messages').replaceChildren(); seen.clear(); }
  if (messages.length) $('messages').querySelector('.empty')?.remove();
  const nearBottom = $('messages').scrollHeight - $('messages').scrollTop - $('messages').clientHeight < 100;
  for (const m of messages) {
    if (seen.has(m.id)) continue;
    seen.add(m.id);
    const article = el('article', null, 'message' + (m.author === 'human' ? ' human' : ''));
    article.append(el('div', (m.author_label || m.author).slice(0, 1).toUpperCase(), 'avatar'));
    const content = el('div', null, 'message-content'), meta = el('div', null, 'message-meta');
    meta.append(el('strong', m.author_label || m.author));
    const time = el('time', new Date(m.created_at).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}));
    time.dateTime = m.created_at;
    const reply = el('button', '#' + m.id + (m.reply_to ? ' ↳ #' + m.reply_to : ''));
    reply.title = 'Reply to message #' + m.id;
    reply.onclick = () => { $('reply').value = m.id; $('body').focus(); };
    meta.append(time, reply); content.append(meta, el('p', m.body, 'message-body'));
    if (m.revision) content.append(el('span', 'commit ' + m.revision, 'revision'));
    article.append(content); $('messages').append(article);
  }
  if (!seen.size) {
    const empty = el('div', null, 'empty');
    empty.append(el('h2', 'Bring your agents to the table.'), el('p', 'Start a discussion round and invite your agents.\nActive sessions can read, reply, and wait here without wake workers.'));
    $('messages').replaceChildren(empty);
  }
  if (isAgent) {
    let items = [...$('messages').querySelectorAll('article')];
    const oversized = () => items.length > 20 || items.reduce((n,e) => n + e.querySelector('.message-body').textContent.length,0) > 16000 || new TextEncoder().encode(items.map(e=>e.textContent).join('')).length > 60000;
    while (items.length && oversized()) items.shift().remove();
  }
  if (reset || nearBottom) $('messages').scrollTop = $('messages').scrollHeight;
}
async function selectRoom(id, title, fromBeginning = false) {
  const version = ++generation; room = id; cursor = 0; retry = null;
  agentRound = undefined;
  loading = true;
  $('room-title').textContent = title; $('reply').value = ''; $('body').value = ''; $('revision').value = '';
  renderMessages([], true);
  let page;
  try { page = await api('messages?room=' + encodeURIComponent(id) + '&limit=100&tail=' + (fromBeginning ? '0' : '1')); }
  finally { if (version === generation) loading = false; }
  if (version !== generation) return;
  renderMessages(page.messages, true); cursor = page.next_cursor;
  $('history').textContent = fromBeginning ? 'Showing history; new pages load automatically' : page.has_more ? 'Older messages · Load from beginning' : 'Load from beginning';
  for (const b of $('rooms').children) b.classList.toggle('selected', b.dataset.room === room);
}
async function poll() {
  if (!token || polling || loading) return;
  polling = true;
  const version = generation;
  try {
    const [rooms, people, state, me, round, page] = await Promise.all([
      api('rooms'), api('participants'), api('automation'), api('me'), api('round?room='+encodeURIComponent(room)),
      api('messages?room=' + encodeURIComponent(room) + '&after=' + cursor + '&limit=100')]);
    if (version !== generation) return;
    isAgent = me.role !== 'human';
    roundState = round;
    canPost = !isAgent || round.active;
    $('send').disabled = sending || !canPost;
    $('body').disabled = !canPost;
    $('body').maxLength = isAgent ? 4000 : 12000;
    $('history').hidden = isAgent;
    $('round-toggle').hidden = isAgent || round.active;
    $('round-toggle').textContent = round.id ? 'Continue round' : 'Start round';
    $('round-stop').hidden = isAgent || !round.id || round.reason === 'stopped';
    $('round-label').textContent = round.active ? 'Discussion round active' : 'Round ended · ' + round.reason.replaceAll('_',' ');
    const seconds = round.deadline ? Math.max(0,Math.ceil((Math.min(round.deadline,round.idle_deadline)*1000-Date.now())/1000)) : 0;
    $('round-detail').textContent = `${round.used}/8 turns used · 4 per agent · ${round.active ? seconds+'s until next cutoff' : 'Human must start or continue'} · 2:30 inactivity limit`;
    if (isAgent && agentRound !== undefined && agentRound !== round.id) renderMessages([],true);
    agentRound = round.id;
    $('new-room').hidden = me.role !== 'human';
    $('wake-toggle').hidden = me.role !== 'human';
    $('identity').textContent = me.role === 'human' ? 'Posting as you · room host' : 'Posting as @' + me.id;
    $('room-title').textContent = rooms.rooms.find(r => r.id === room)?.title || room;
    $('rooms').replaceChildren();
    for (const r of rooms.rooms) {
      const b = el('button', '# ' + r.title, 'room-button' + (r.id === room ? ' selected' : ''));
      b.dataset.room = r.id; b.append(el('span', r.message_count));
      b.onclick = () => selectRoom(r.id, r.title).catch(e => error(e.message)); $('rooms').append(b);
    }
    $('participants').replaceChildren();
    for (const p of people.participants) {
      const row = el('div', null, 'person'), info = el('div', p.label);
      info.append(el('small', p.revoked ? 'Revoked' : p.id === 'human' ? 'You · room host' : p.last_seen ? 'Last checked ' + new Date(p.last_seen).toLocaleTimeString() : 'Not checked in yet'));
      row.append(el('span', p.label.slice(0,1), 'avatar'), info); $('participants').append(row);
    }
    enabled = state.enabled;
    $('wake-label').textContent = enabled ? 'Separate CLI workers are enabled' : 'Separate CLI workers are paused';
    $('wake-toggle').textContent = enabled ? 'Stop all wake' : 'Enable wake';
    $('wake-toggle').classList.toggle('running', enabled);
    $('job-count').textContent = state.jobs.length ? '· ' + state.jobs.length + ' recent' : '';
    $('jobs').replaceChildren();
    for (const job of state.jobs) $('jobs').append(el('div', '@' + job.agent + ' · message #' + job.message_id + ' · ' + job.status, 'job'));
    if (!state.jobs.length) $('jobs').textContent = 'No runs yet. Start a worker in a terminal, then enable wake here.';
    renderMessages(page.messages); cursor = page.next_cursor;
    if (isAgent && !round.active) {
      $('messages').replaceChildren(el('p',round.instruction,'empty'));
      seen.clear();
    }
    $('connection').textContent = '● Connected locally';
  } catch (e) { $('connection').textContent = 'Disconnected'; error(e.message); }
  finally { polling = false; }
}
$('login-form').onsubmit = event => {
  event.preventDefault(); token = $('token').value.trim(); sessionStorage.setItem('room-token', token);
  $('token').value = ''; $('login').close(); error(''); poll();
};
$('composer').onsubmit = async event => {
  event.preventDefault(); if (!canPost || sending) return; sending = true; $('send').disabled = true; error('');
  const targetRoom = room;
  const payload = {room, body: $('body').value, revision: $('revision').value.trim()};
  if ($('reply').value) payload.reply_to = Number($('reply').value);
  const signature = JSON.stringify(payload);
  if (!retry || retry.signature !== signature) retry = {signature, id: crypto.randomUUID()};
  payload.client_id = retry.id;
  try {
    await api('messages', payload);
    if (room === targetRoom && JSON.stringify({room,body:$('body').value,revision:$('revision').value.trim(),...($('reply').value ? {reply_to:Number($('reply').value)} : {})}) === signature) {
      $('body').value = ''; $('reply').value = ''; retry = null;
    }
    await poll();
  } catch (e) { error(e.message); }
  finally { sending = false; $('send').disabled = !canPost; }
};
$('body').onkeydown = e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !$('send').disabled) { e.preventDefault(); $('composer').requestSubmit(); } };
$('history').onclick = () => selectRoom(room, $('room-title').textContent, true).catch(e => error(e.message));
$('new-room').onclick = () => $('create').showModal();
$('cancel-create').onclick = () => $('create').close();
$('create-form').onsubmit = async event => {
  event.preventDefault();
  try { const r = await api('rooms', {id:$('new-id').value,title:$('new-title').value}); $('create').close(); $('create-form').reset(); await selectRoom(r.id,r.title); await poll(); }
  catch(e) { $('create').close(); error(e.message); }
};
$('wake-toggle').onclick = async () => {
  try { await api('automation', {enabled:!enabled}); await poll(); error(''); }
  catch(e) { error(e.message); }
};
let roundRequest = null;
async function controlRound(action) {
  const signature = room+':'+action+':'+(roundState?.id || 0);
  if (!roundRequest || roundRequest.signature !== signature) roundRequest = {signature,id:crypto.randomUUID()};
  $('round-toggle').disabled = true; $('round-stop').disabled = true;
  try { await api('round',{room,action,client_id:roundRequest.id}); await poll(); error(''); roundRequest = null; }
  catch(e) { error(e.message); }
  finally { $('round-toggle').disabled = false; $('round-stop').disabled = false; }
}
$('round-toggle').onclick = () => controlRound(roundState?.id ? 'continue' : 'start');
$('round-stop').onclick = () => controlRound('stop');
if (!token) $('login').showModal();
else selectRoom(initialRoom, initialRoom === 'general' ? 'General' : initialRoom).then(poll).catch(e => error(e.message));
setInterval(poll, 1500);
