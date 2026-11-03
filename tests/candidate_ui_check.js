"use strict";
// 执行实际app.js和事件处理器；DOM/API为替身，不代替浏览器视觉验收。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor(tag = "div") { this.tag = tag; this.children = []; this.events = new Map(); this.dataset = {}; this.className = ""; this._text = ""; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((n) => n !== this); }
  querySelectorAll(selector) {
    return this.children.flatMap((node) => [(selector.startsWith(".") ? node.className.split(" ").includes(selector.slice(1)) : node.tag === selector) ? node : null,
      ...node.querySelectorAll(selector)]).filter(Boolean);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, handler, options = {}) {
    this.events.set(type, [...(this.events.get(type) || []), {handler, once: options.once}]);
  }
  async emit(type) {
    const listeners = this.events.get(type) || [];
    this.events.set(type, listeners.filter((l) => !l.once));
    await Promise.all(listeners.map(({handler}) => handler({target: this, preventDefault() {}})));
  }
  showModal() { this.open = true; }
}
const elements = new Map();
const get = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
const document = {body: {dataset: {apiRoot: "/api"}}, getElementById: get, createElement: (tag) => new Element(tag), querySelectorAll: () => []};
const row = (id, archive, field, value, content) => ({id, archive_id: archive, status: "pending", base_version: 1,
  created_at: "2026-01-01T00:00:00Z", source_conversation: {title: "虚构会话"}, source_message: {content},
  operations: [{op: "set_finances", currency: "CNY", values: {[field]: value}}]});
const rows = [row("a", "batch-a", "investment_budget", "9000000.00", "投资预算900，单位万元"),
  row("b", "batch-b", "cash", "3000.00", "现金三千"), row("c", null, "investment_budget", "950.00", "投资九百五")];
const profile = {version: 1, currency: "CNY", risk_preference: null, goals: [], holdings: [], investment_budget: "800.00"};
const writes = [];
const conflict = "档案版本冲突：基于 1，当前 2；请重新读取并确认";
async function fetch(url, options) {
  let data;
  if (options.method === "GET") data = url === "/api/profile" ? profile : rows;
  else {
    writes.push(url);
    if (url === "/api/profile-change-batches/batch-a/confirm") { rows[0].status = "confirmed"; profile.version = 2; data = {}; }
    else if (url.endsWith("/reject")) { rows.find((r) => url.includes(`/${r.id}/`)).status = "rejected"; data = {}; }
    else return {ok: false, status: 409, json: async () => ({error: {code: "version_conflict", message: conflict}})};
  }
  return {ok: true, status: 200, json: async () => ({data: structuredClone(data)})};
}
const context = {document, fetch, console, Option: function(text, value) { const e = new Element("option"); e.textContent = text; e.value = value; return e; }};
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), context);
const buttons = () => get("changes-list").querySelectorAll("button");
const batchButton = () => buttons().find((b) => b.textContent.startsWith("合并确认"));
const singleButton = (label) => buttons().find((b) => b.textContent === label && b.parent.parent.textContent.includes("投资九百五"));
const finishDialog = async (choice) => { get("confirm-dialog").returnValue = choice; await get("confirm-dialog").emit("close"); };
const assertCleared = () => { assert.equal(get("app-status").textContent, ""); assert.equal(get("changes-status").textContent, ""); };

(async () => {
  await get("changes-reload").emit("click");
  assert.match(get("changes-list").textContent, /9000000.00 CNY/);
  get("app-status").textContent = "上一轮成功";
  get("changes-status").textContent = "上一轮错误";
  let action = batchButton().emit("click");
  assertCleared();
  assert.match(get("confirm-text").textContent, /9000000.00 CNY/);
  assert.match(get("confirm-text").textContent, /投资预算900，单位万元/);
  await finishDialog("confirm"); await action;
  assert.match(get("app-status").textContent, /成功/);

  action = batchButton().emit("click");
  assertCleared();
  await finishDialog("confirm"); await action;
  assert.equal(get("app-status").textContent, "");
  assert.ok(get("changes-status").textContent.includes(conflict));
  assert.ok(!get("changes-status").textContent.includes("成功"));

  action = singleButton("确认修改").emit("click");
  assertCleared();
  assert.match(get("confirm-text").textContent, /950.00 CNY/);
  assert.match(get("confirm-text").textContent, /投资九百五/);
  await finishDialog("confirm"); await action;
  assert.ok(get("changes-status").textContent.includes(conflict));

  let count = writes.length;
  action = singleButton("确认修改").emit("click"); assertCleared();
  await finishDialog("cancel"); await action;
  assert.equal(writes.length, count);
  assert.match(get("changes-status").textContent, /已取消/);
  action = batchButton().emit("click"); assertCleared();
  await finishDialog("cancel"); await action;
  assert.equal(writes.length, count);

  get("app-status").textContent = "旧成功";
  get("changes-status").textContent = conflict;
  action = singleButton("拒绝此变更").emit("click"); assertCleared(); await action;
  assert.match(get("app-status").textContent, /已拒绝/);
  assert.ok(!get("changes-status").textContent.includes(conflict));
  assert.equal(writes.length, count + 1);
  console.log("candidate feedback, dialogs and cancel: OK (DOM/API doubles)");
})().catch((error) => { console.error(error); process.exitCode = 1; });
