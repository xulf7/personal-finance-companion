"use strict";

// 所有金额和数量从 input.value 原样取得；只把空白规范为 null，不做浮点转换。
(() => {
  const $ = (id) => document.getElementById(id);
  const root = document.body.dataset.apiRoot;
  const finance = ["income", "expenses", "cash", "debt", "savings_budget", "investment_budget"];
  const labels = {income: "每月收入", expenses: "每月支出", cash: "当前现金", debt: "当前负债", savings_budget: "每月储蓄预算", investment_budget: "每月投资预算"};
  const riskNames = {low: "低风险", medium: "中等风险", high: "高风险"};
  const summaryNames = {facts: "重要事实", decisions: "历史决策", reasons: "原因", plans: "假设或计划", confirmed: "当时确认的状态（不等于当前档案）", todos: "待办", unresolved: "未决事项"};
  const state = {profile: null, dirty: false, saving: false, profileRequest: 0, conversations: [], active: "", sessionRequest: 0, listRequest: 0, sending: false, archiving: false, archives: new Map(), drafts: new Map(), chatTurns: new Map(), chatRequests: new Map(), changesRequest: 0, changesBusy: false, market: new Map(), loaded: new Set()};

  function node(tag, content, className) {
    const item = document.createElement(tag);
    if (content !== undefined) item.textContent = content;
    if (className) item.className = className;
    return item;
  }
  function say(id, message, kind = "") {
    $(id).textContent = message;
    $(id).className = kind ? `feedback ${kind}` : "";
  }
  const nullable = (value) => value.trim() === "" ? null : value.trim();
  const shown = (value) => value === null || value === undefined ? "未填写" : String(value);
  const time = (value) => value ? value.replace("T", " ").replace("Z", " UTC") : "—";
  function dirty() { state.dirty = true; }
  function same(a, b, keys) { return b && keys.every((key) => a[key] === b[key]); }

  async function api(path, method = "GET", value) {
    let result;
    try {
      result = await fetch(root + path, {method, cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json", ...(value === undefined ? {} : {"Content-Type": "application/json"})}, ...(value === undefined ? {} : {body: JSON.stringify(value)})});
    } catch {
      throw new Error("无法连接本地服务，请检查应用是否仍在运行。输入已保留。");
    }
    let payload;
    try { payload = await result.json(); } catch { throw new Error("服务返回了无法识别的响应，请稍后重试。"); }
    if (!result.ok) {
      const failure = new Error(payload.error?.message || "操作未完成。");
      failure.status = result.status;
      failure.code = payload.error?.code;
      throw failure;
    }
    return payload.data;
  }

  function confirmAction(title, description) {
    const dialog = $("confirm-dialog");
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = description;
    dialog.returnValue = "";
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once: true});
      dialog.showModal();
    });
  }

  function currencyOptions(select, selected, blank = false) {
    const codes = ["CNY", "USD", "HKD", "EUR", "GBP", "JPY"];
    const names = {CNY: "人民币 CNY", USD: "美元 USD", HKD: "港币 HKD", EUR: "欧元 EUR", GBP: "英镑 GBP", JPY: "日元 JPY"};
    if (selected && !codes.includes(selected)) codes.push(selected);
    select.replaceChildren();
    if (blank) select.append(new Option("未填写", ""));
    for (const code of codes) select.append(new Option(names[code] || code, code));
    select.value = selected || "";
  }
  function control(row, field, title, value, type = "text", required = false) {
    const label = node("label", title);
    const input = document.createElement(type === "currency" ? "select" : "input");
    input.dataset.field = field;
    if (type === "currency") currencyOptions(input, value || "CNY");
    else { input.type = type; input.value = value ?? ""; input.placeholder = "未填写"; }
    if (["target_amount", "quantity", "unit_cost"].includes(field)) input.inputMode = "decimal";
    input.required = required;
    label.append(input);
    row.append(label);
  }
  function emptyRows(kind) {
    const list = $(kind === "goal" ? "goals" : "holdings");
    list.querySelector(".empty")?.remove();
    if (!list.querySelector("[data-item]")) list.append(node("p", kind === "goal" ? "暂无财务目标。" : "暂无持仓。", "empty"));
  }
  function addRow(kind, value = {}) {
    const row = node("fieldset", undefined, "item-row");
    row.dataset.item = kind;
    row.dataset.id = value.id || crypto.randomUUID();
    row.append(node("legend", kind === "goal" ? "财务目标" : "持仓记录"));
    const grid = node("div", undefined, "form-grid");
    row.append(grid);
    if (kind === "goal") {
      control(grid, "name", "目标名称", value.name, "text", true);
      control(grid, "target_amount", "目标金额", value.target_amount);
      control(grid, "currency", "目标币种", value.currency || state.profile?.currency, "currency");
      control(grid, "target_date", "目标日期", value.target_date, "date");
    } else {
      control(grid, "asset_code", "资产名称或代码", value.asset_code, "text", true);
      control(grid, "quantity", "持仓数量", value.quantity);
      control(grid, "unit_cost", "单位成本", value.unit_cost);
      control(grid, "currency", "持仓币种", value.currency || state.profile?.currency, "currency");
    }
    const remove = node("button", kind === "goal" ? "删除此目标" : "删除此持仓", "danger secondary");
    remove.type = "button";
    remove.addEventListener("click", async () => {
      if (await confirmAction("确认删除？", "该项将从当前草稿移除，点击“保存财务档案”后才写入数据库。")) {
        row.remove(); dirty(); emptyRows(kind);
      }
    });
    row.append(remove);
    const list = $(kind === "goal" ? "goals" : "holdings");
    list.querySelector(".empty")?.remove();
    list.append(row);
    return row;
  }
  function installProfile(profile) {
    state.profile = profile;
    state.dirty = false;
    for (const field of finance) $(field).value = profile[field] ?? "";
    currencyOptions($("profile-currency"), profile.currency, true);
    $("profile-currency").disabled = Boolean(profile.currency);
    $("profile-risk").value = profile.risk_preference ?? "";
    $("profile-version").textContent = `档案版本 ${profile.version} · 更新于 ${time(profile.updated_at)}`;
    $("goals").replaceChildren(); $("holdings").replaceChildren();
    profile.goals.forEach((goal) => addRow("goal", goal));
    profile.holdings.forEach((holding) => addRow("holding", holding));
    emptyRows("goal"); emptyRows("holding");
    $("profile-fields").disabled = false;
  }
  async function loadProfile(preserveDirty = true) {
    const ticket = ++state.profileRequest;
    say("profile-status", "正在读取档案…");
    if (!preserveDirty) $("profile-fields").disabled = true;
    try {
      const profile = await api("/profile");
      if (ticket !== state.profileRequest) return;
      if (preserveDirty && state.dirty) {
        $("profile-version").textContent = `当前服务器版本 ${profile.version}；草稿仍基于版本 ${state.profile.version}`;
        say("profile-status", "最新档案已读取，未覆盖你的草稿。请点击重新读取档案，核对后再保存。", "warning");
      } else { installProfile(profile); say("profile-status", "档案已读取。", "success"); }
    } catch (error) { if (ticket === state.profileRequest) say("profile-status", error.message, "error"); }
    finally { if (ticket === state.profileRequest && state.profile) $("profile-fields").disabled = false; }
  }
  function profileChanges() {
    const profile = state.profile;
    const changes = [];
    const values = Object.fromEntries(finance.map((key) => [key, nullable($(key).value)]));
    const currency = $("profile-currency").value;
    if (currency !== (profile.currency ?? "") || finance.some((key) => values[key] !== profile[key])) {
      if (!currency) throw new Error("请先选择基础财务币种。输入已保留。");
      changes.push({op: "set_finances", currency, values});
    }
    const risk = $("profile-risk").value || null;
    if (risk !== profile.risk_preference) changes.push({op: "set_risk", value: risk});
    for (const kind of ["goal", "holding"]) {
      const original = profile[kind === "goal" ? "goals" : "holdings"];
      const seen = new Set();
      const keys = kind === "goal" ? ["id", "name", "target_amount", "currency", "target_date"] : ["id", "asset_code", "quantity", "unit_cost", "currency"];
      for (const row of document.querySelectorAll(`[data-item="${kind}"]`)) {
        const value = {id: row.dataset.id};
        for (const key of keys.slice(1)) value[key] = nullable(row.querySelector(`[data-field="${key}"]`).value);
        seen.add(value.id);
        if (!same(value, original.find((item) => item.id === value.id), keys)) changes.push({op: `set_${kind}`, [kind]: value});
      }
      for (const item of original) if (!seen.has(item.id)) changes.push({op: `remove_${kind}`, id: item.id});
    }
    return changes;
  }
  $("profile-form").addEventListener("input", dirty);
  $("profile-form").addEventListener("change", dirty);
  $("goal-add").addEventListener("click", () => { addRow("goal").querySelector("input").focus(); dirty(); });
  $("holding-add").addEventListener("click", () => { addRow("holding").querySelector("input").focus(); dirty(); });
  $("profile-reload").addEventListener("click", async () => {
    if (state.saving) return;
    if (!state.dirty || await confirmAction("重新读取档案？", "当前未保存的草稿将被最新档案替换，请先记录需要保留的输入。")) await loadProfile(false);
  });
  $("profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.saving || !state.profile) return;
    let changes;
    try { changes = profileChanges(); } catch (error) { say("profile-status", error.message, "error"); return; }
    if (!changes.length) { say("profile-status", "没有需要保存的修改。"); return; }
    state.saving = true;
    $("profile-fields").disabled = true; $("profile-reload").disabled = true;
    say("profile-status", "正在保存…");
    try {
      const profile = await api("/profile", "PATCH", {expected_version: state.profile.version, changes});
      ++state.profileRequest;
      installProfile(profile);
      say("profile-status", `保存成功，当前档案版本 ${profile.version}。`, "success");
    } catch (error) {
      say("profile-status", error.status === 409 ? "档案已发生变化，输入已保留。请重新读取最新档案后核对，不会自动重试。" : error.message, "error");
    } finally { state.saving = false; $("profile-fields").disabled = false; $("profile-reload").disabled = false; }
  });

  function drawSummary(container, content) {
    for (const [key, title] of Object.entries(summaryNames)) {
      if (!content[key]?.length) continue;
      container.append(node("h4", title));
      const list = node("ul");
      content[key].forEach((value) => list.append(node("li", value, "preserve-text")));
      container.append(list);
    }
  }
  async function loadOverview() {
    try {
      const overview = await api("/overview");
      $("overview").replaceChildren();
      if (!overview) { $("overview").append(node("p", "暂无历史决策概览。", "empty")); return; }
      $("overview").append(node("p", `概览第 ${overview.version} 版 · ${time(overview.created_at)} · 依据 ${overview.summary_ids.length} 份历史摘要`));
      drawSummary($("overview"), overview.content);
    } catch (error) { $("overview").replaceChildren(node("p", error.message, "feedback error")); }
  }
  async function loadSession(conversationId) {
    const ticket = ++state.sessionRequest;
    $("messages").replaceChildren(node("p", "正在读取消息…", "empty"));
    $("summaries").replaceChildren();
    $("archive-generate").disabled = true;
    say("archive-status", "正在检查可归档范围…");
    if (!conversationId) { $("messages").replaceChildren(node("p", "暂无会话，请先新建会话。", "empty")); say("chat-status", ""); say("archive-status", "选择会话后检查可归档范围。"); return; }
    say("chat-status", "正在读取会话…");
    try {
      const base = `/conversations/${encodeURIComponent(conversationId)}`;
      const [history, summaries, archive] = await Promise.all([api(`${base}/chat`), api(`${base}/summaries`), api(`${base}/archive`)]);
      if (ticket !== state.sessionRequest || conversationId !== state.active) return;
      const {messages, turns} = history;
      state.archives.set(conversationId, archive);
      const pendingArchive = archive.pending;
      const latestArchive = archive.latest;
      const previousArchive = latestArchive ? `已归档 #${latestArchive.first_sequence}–#${latestArchive.last_sequence}，摘要第 ${latestArchive.summary.version} 版，新建 ${latestArchive.candidate_count} 个待确认项（来源及当前状态见待确认变更页）。` : "";
      const archiveButton = $("archive-generate");
      archiveButton.textContent = pendingArchive?.can_retry ? "重试归档（可能计费）" : "生成归档摘要";
      archiveButton.disabled = state.archiving || !(pendingArchive?.can_retry || archive.can_archive);
      say("archive-status", state.archiving ? "正在归档，请勿重复点击；可继续查看会话。" : pendingArchive ? `${previousArchive} ${pendingArchive.error?.message || `正在归档 #${pendingArchive.first_sequence}–#${pendingArchive.last_sequence}；可重新读取状态，服务中断后 ${time(pendingArchive.lease_until)} 起可明确重试。`}` : `${previousArchive} ${archive.next_range ? `下次范围 #${archive.next_range.first_sequence}–#${archive.next_range.last_sequence}。` : ""} ${archive.notice}`, pendingArchive?.error ? "error" : latestArchive ? "success" : "");
      state.chatTurns.set(conversationId, turns);
      const unfinished = turns.find((turn) => turn.status !== "succeeded");
      const turnFor = new Map(turns.map((turn) => [turn.user_message.id, turn]));
      const uncertain = state.chatRequests.get(conversationId);
      if (uncertain && turns.some((turn) => turn.id === uncertain.id)) {
        state.chatRequests.delete(conversationId);
        if (state.drafts.get(conversationId) === uncertain.content) state.drafts.set(conversationId, "");
        if ($("message-text").value === uncertain.content) $("message-text").value = state.drafts.get(conversationId) || "";
      }
      $("messages").replaceChildren();
      if (!messages.length) $("messages").append(node("p", "当前会话还没有消息。", "empty"));
      for (const message of messages) {
        const row = node("article", undefined, `message message-${message.role}`);
        row.append(node("p", `#${message.sequence} · ${{user: "用户", assistant: "助手", system: "系统（历史记录）", tool: "工具（历史记录）"}[message.role]} · ${time(message.created_at)}`, "meta"), node("p", message.content, "preserve-text"));
        const turn = turnFor.get(message.id);
        if (turn?.status === "running") row.append(node("p", `正在等待模型回复；可重新读取会话。若服务中断，${time(turn.lease_until)} 后可明确重试。`, "feedback warning"));
        if (turn?.error) row.append(node("p", `尚无已保存的助手回复：${turn.error.message}`, "feedback error"));
        if (turn?.can_retry) {
          const retry = node("button", "重试这条问题（可能计费）", "secondary");
          retry.disabled = state.sending;
          retry.addEventListener("click", () => sendChat(conversationId, null, turn.id));
          row.append(retry);
        }
        if (message.role === "assistant") {
          const source = turns.find((item) => item.assistant_message?.id === message.id);
          if (source) row.append(node("p", `模型：${source.model || "响应未提供"} · Token：${source.usage ? Object.entries(source.usage).map(([k, v]) => `${k}=${v}`).join("，") : "响应未提供，不估算"}`, "meta"));
        }
        $("messages").append(row);
      }
      if (!summaries.length) $("summaries").append(node("p", "暂无摘要。完整轮次可点击生成归档摘要；普通发送不会自动归档。", "empty"));
      for (const summary of summaries) {
        const detail = node("details");
        detail.open = summary.id === latestArchive?.summary?.id;
        detail.append(node("summary", `摘要第 ${summary.version} 版 · 覆盖消息 #${summary.first_sequence}–#${summary.last_sequence}`));
        drawSummary(detail, summary.content); $("summaries").append(detail);
      }
      $("message-send").disabled = state.sending || !!unfinished;
      $("message-text").disabled = !!unfinished;
      say("chat-status", unfinished ? "当前问题未完成，请等待或使用原问题的重试按钮；原文已保存。" : `已读取 ${messages.length} 条消息。发送问题会调用模型。`, unfinished ? "warning" : "success");
    } catch (error) { if (ticket === state.sessionRequest && conversationId === state.active) { $("messages").replaceChildren(); say("chat-status", error.message, "error"); say("archive-status", `无法读取归档状态：${error.message}`, "error"); } }
  }
  function selectConversation(id) {
    if (state.active) state.drafts.set(state.active, $("message-text").value);
    state.active = id;
    $("conversation-select").value = id;
    $("message-text").value = state.drafts.get(id) || "";
    $("message-text").disabled = !id;
    $("message-send").disabled = !id || state.sending;
    return loadSession(id);
  }
  async function loadConversations(preferred) {
    const ticket = ++state.listRequest;
    say("chat-status", "正在读取会话列表…");
    try {
      const conversations = await api("/conversations");
      if (ticket !== state.listRequest) return;
      state.conversations = conversations;
      const select = $("conversation-select"); select.replaceChildren();
      conversations.forEach((value) => select.append(new Option(value.title, value.id)));
      select.disabled = !conversations.length;
      const selected = preferred || state.active;
      await selectConversation(conversations.some((c) => c.id === selected) ? selected : (conversations[0]?.id || ""));
      await loadOverview();
    } catch (error) { if (ticket === state.listRequest) say("chat-status", error.message, "error"); }
  }
  $("conversation-select").addEventListener("change", (event) => selectConversation(event.target.value));
  $("chat-reload").addEventListener("click", () => loadConversations());
  $("conversation-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button");
    if (button.disabled) return;
    const title = $("conversation-title").value;
    button.disabled = true;
    try {
      const created = await api("/conversations", "POST", {title});
      if ($("conversation-title").value === title) $("conversation-title").value = "";
      await loadConversations(created.id);
    } catch (error) { say("chat-status", error.message, "error"); }
    finally { button.disabled = false; }
  });
  $("message-text").addEventListener("input", () => { if (state.active) state.drafts.set(state.active, $("message-text").value); });
  async function sendChat(id, content, retryId = null) {
    if (state.sending || !id) return;
    if (retryId && !await confirmAction("重试原问题", "将使用已保存的用户原文重新请求模型，不再插入用户消息；可能再次计费。")) return;
    if (state.sending) return;
    const title = state.conversations.find((c) => c.id === id)?.title || "原会话";
    if (!retryId) state.drafts.set(id, content);
    const previous = state.chatRequests.get(id);
    const request = retryId ? null : (previous?.content === content ? previous : {id: crypto.randomUUID(), content});
    if (request) state.chatRequests.set(id, request); // HTTP 结果不确定时再次提交沿用同一标识。
    state.sending = true; $("message-send").disabled = true;
    say("chat-status", "正在等待模型回复，请勿重复点击…");
    try {
      const base = `/conversations/${encodeURIComponent(id)}/chat`;
      const turn = retryId ? await api(`${base}/${encodeURIComponent(retryId)}/retry`, "POST", {}) : await api(base, "POST", {content, request_id: request.id});
      if (request) state.chatRequests.delete(id);
      if (!retryId && state.drafts.get(id) === content) state.drafts.set(id, "");
      if (state.active === id) $("message-text").value = state.drafts.get(id) || "";
      say("app-status", turn.status === "succeeded" ? `「${title}」的用户消息与助手回复均已保存。` : `「${title}」尚未取得已保存的回复；请查看原问题状态。`, turn.status === "succeeded" ? "success" : "warning");
    } catch (error) {
      say("app-status", `${error.message} 请重新读取会话确认状态；若原请求尚未保存，再次发送相同草稿会沿用请求标识。`, "error");
    } finally {
      state.sending = false;
      if (state.active) await loadSession(state.active);
    }
  }
  $("message-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.sending || !state.active || $("message-send").disabled) return;
    await sendChat(state.active, $("message-text").value);
  });

  $("archive-generate").addEventListener("click", async () => {
    const id = state.active;
    const archive = state.archives.get(id);
    if (!id || !archive || state.archiving || $("archive-generate").disabled) return;
    const retry = archive.pending?.can_retry ? archive.pending.id : null;
    if (!await confirmAction(retry ? "明确重试归档？" : "生成归档摘要？", "将调用外部模型，可能计费。保存结构化摘要和跨会话记忆；有可核对依据时仅生成待确认候选，确认前不改变财务档案。")) return;
    if (state.archiving) return;
    state.archiving = true;
    $("archive-generate").disabled = true;
    say("archive-status", "正在生成并校验归档摘要，请勿重复点击…");
    try {
      const base = `/conversations/${encodeURIComponent(id)}/archive`;
      const result = retry ? await api(`${base}/${encodeURIComponent(retry)}/retry`, "POST", {}) : await api(base, "POST", archive.next_range);
      say("app-status", result.status === "succeeded" ? `归档成功：消息 #${result.first_sequence}–#${result.last_sequence}，新建 ${result.candidate_count} 个待确认项。当前档案未改变。` : result.error?.message || "归档仍在处理中，请重新读取状态。", result.status === "succeeded" ? "success" : "warning");
      state.loaded.delete("changes");
      if (state.active === id) $("summaries").parentElement.open = true;
    } catch (error) {
      say("app-status", `${error.message} 原聊天及先前归档保留，请重新读取会话确认状态。`, "error");
    } finally {
      state.archiving = false;
      if (state.active) await loadSession(state.active);
      await loadOverview();
      if (!$("panel-changes").hidden) await loadChanges();
    }
  });

  function describeOperations(operations) {
    return operations.map((change) => {
      if (change.op === "set_finances") return Object.entries(change.values).map(([key, value]) => `${labels[key] || key}：${value === null ? "未知（清空该数值）" : shown(value)} ${change.currency}`).join("\n");
      if (change.op === "set_risk") return `风险偏好：${riskNames[change.value] || "未填写"}`;
      if (change.op === "set_goal") { const g = change.goal; return `保存财务目标：${g.name}\n目标金额：${shown(g.target_amount)} ${g.currency}\n目标日期：${shown(g.target_date)}`; }
      if (change.op === "set_holding") { const h = change.holding; return `保存持仓：${h.asset_code}\n数量：${shown(h.quantity)}\n单位成本：${shown(h.unit_cost)} ${h.currency}`; }
      const items = change.op === "remove_goal" ? state.profile?.goals : state.profile?.holdings;
      const found = items?.find((item) => item.id === change.id);
      return `删除${change.op === "remove_goal" ? "财务目标" : "持仓"}：${found?.name || found?.asset_code || `编号 ${change.id}`}`;
    }).join("\n\n");
  }
  function setChangesBusy(value) {
    state.changesBusy = value;
    $("changes-list").querySelectorAll("button").forEach((button) => { button.disabled = value; });
  }
  function beginChangesOperation() {
    setChangesBusy(true);
    say("app-status", "");
    say("changes-status", "");
  }
  async function loadChanges() {
    const ticket = ++state.changesRequest;
    say("changes-status", "正在读取候选…");
    try {
      const rows = await api("/profile-changes");
      if (ticket !== state.changesRequest) return;
      $("changes-list").replaceChildren();
      if (!rows.length) $("changes-list").append(node("p", "暂无候选。可在对话记录中显式归档，生成有原文依据的候选。", "empty"));
      const batches = new Map();
      for (const change of rows) {
        if (change.archive_id && !batches.has(change.archive_id)) {
          const members = rows.filter((row) => row.archive_id === change.archive_id);
          const pending = members.filter((row) => row.status === "pending");
          const group = node("article", undefined, "card");
          group.append(node("h3", `同批次归档候选 · ${change.source_conversation.title}`), node("p", `批次：${change.archive_id}`, "meta preserve-text"));
          if (pending.length) {
            group.append(node("p", "请先逐条拒绝不想应用的项，再合并确认全部剩余项。单条确认仍可用，但可能使同批次其他项发生版本冲突。", "hint"));
            const confirmBatch = node("button", `合并确认剩余 ${pending.length} 项`);
            confirmBatch.disabled = state.changesBusy;
            confirmBatch.addEventListener("click", async () => {
              if (state.changesBusy) return;
              beginChangesOperation();
              try {
                const description = pending.map((row, index) => `${index + 1}. ${describeOperations(row.operations)}\n原始用户消息：${row.source_message.content}\n基于档案版本 ${row.base_version}`).join("\n\n");
                if (!await confirmAction("合并确认以下全部变更？", `${description}\n\n请核对模型是否正确理解原文及金额单位。一次校验版本并更新档案；同一字段有不同目标值时将拒绝，不会覆盖。`)) {
                  say("changes-status", "已取消确认，未写入。");
                  return;
                }
                await api(`/profile-change-batches/${encodeURIComponent(change.archive_id)}/confirm`, "POST", {change_ids: pending.map((row) => row.id)});
                await loadChanges();
                await loadProfile(true);
                say("app-status", "批次合并确认成功。档案页如有未保存草稿，请重新读取后核对。", "success");
              } catch (error) {
                say("changes-status", `${error.message} 请重新读取候选并核对；不会自动改版本重试。`, "error");
              } finally { setChangesBusy(false); }
            });
            group.append(confirmBatch);
          }
          batches.set(change.archive_id, group);
          $("changes-list").append(group);
        }
      }
      for (const change of rows) {
        const card = node("article", undefined, "candidate card");
        const status = {pending: "待确认", confirmed: "已确认", rejected: "已拒绝"}[change.status];
        card.append(node("h3", status), node("p", "拟写入值（金额为币种基本单位，请与原文核对）：", "hint"), node("p", describeOperations(change.operations), "preserve-text"), node("p", `来源会话：${change.source_conversation.title}`), node("blockquote", change.source_message.content, "preserve-text"), node("p", `基于档案版本 ${change.base_version} · 创建于 ${time(change.created_at)}`, "meta"));
        if (change.processed_at) card.append(node("p", `处理时间：${time(change.processed_at)}${change.applied_version === null ? "" : ` · 应用后版本 ${change.applied_version}`}`, "meta"));
        if (change.status === "pending") {
          const actions = node("div", undefined, "actions");
          const confirm = node("button", "确认修改"); const reject = node("button", "拒绝此变更", "secondary");
          confirm.disabled = state.changesBusy; reject.disabled = state.changesBusy;
          const handle = async (action) => {
            if (state.changesBusy) return;
            beginChangesOperation();
            try {
              if (action === "confirm" && !await confirmAction("确认应用以下修改？", `${describeOperations(change.operations)}\n\n原始用户消息：${change.source_message.content}\n\n请核对模型是否正确理解原文及金额单位。仅在确认后写入当前档案；旧版本冲突将拒绝保存。`)) {
                say("changes-status", "已取消确认，未写入。");
                return;
              }
              await api(`/profile-changes/${encodeURIComponent(change.id)}/${action}`, "POST", {});
              actions.remove();
              card.querySelector("h3").textContent = action === "confirm" ? "已确认" : "已拒绝";
              await loadChanges();
              if (action === "confirm") await loadProfile(true);
              say("app-status", action === "confirm" ? "变更已确认。档案页如有未保存草稿，请重新读取后核对。" : "候选已拒绝，当前档案未改变。", "success");
            } catch (error) { say("changes-status", error.message, "error"); }
            finally { setChangesBusy(false); }
          };
          confirm.addEventListener("click", () => handle("confirm")); reject.addEventListener("click", () => handle("reject"));
          actions.append(confirm, reject); card.append(actions);
        }
        (batches.get(change.archive_id) || $("changes-list")).append(card);
      }
      say("changes-status", `已读取 ${rows.length} 条候选记录。`);
    } catch (error) { if (ticket === state.changesRequest) say("changes-status", error.message, "error"); }
  }
  $("changes-reload").addEventListener("click", loadChanges);

  const metricNames = {price: "股票价格", nav: "基金净值", index_level: "指数点位"};
  const refreshNames = {not_requested: "仅查看缓存", not_needed: "命中有效缓存", updated: "刷新成功", failed: "本次刷新失败", superseded: "本次结果未覆盖已有缓存"};
  function drawMarket(model) {
    const {asset, result, loading, error} = model;
    const row = result?.data;
    const simulated = row?.is_simulated ?? asset.is_simulated;
    model.card.replaceChildren(node("h3", asset.display_name), node("span", simulated ? "模拟数据" : "真实日线数据（非实时）", "badge"));
    const provider = row?.provider_id ?? asset.provider_id;
    const upstream = {stock: "东方财富 · 不复权日线收盘", index: "新浪财经 · 日线收盘", fund: "天天基金/东方财富 · 正式单位净值"};
    model.card.append(node("p", `来源：${provider}${simulated ? "" : ` / AKShare / ${upstream[asset.asset_type]}`}`));
    model.card.append(node("p", metricNames[asset.metrics[0].metric]), node("p", row ? row.value : "暂无数据", "quote-value"));
    if (row) {
      const unit = {currency_per_share: `${row.currency}/股`, currency_per_unit: `${row.currency}/份`, points: "点（无币种）"}[row.unit];
      model.card.append(node("p", `单位：${unit}`), node("p", row.data_time_kind === "date" ? `数据日期：${row.data_time}（仅日期）` : `数据时间：${time(row.data_time)}`), node("p", `获取时间：${time(row.fetched_at)}`, "meta"), node("p", `过期时间：${time(row.expires_at)}`, "meta"));
    }
    if (result) {
      model.card.append(node("p", {fresh: "缓存有效（不代表实时）", stale: "缓存已过期，当前展示旧值", missing: "暂无可用缓存"}[result.cache_status], result.cache_status === "stale" ? "warning-text" : ""), node("p", `刷新结果：${refreshNames[result.refresh_status]}`));
      if (result.error) model.card.append(node("p", result.error.message, "feedback warning"));
    }
    if (error) model.card.append(node("p", error, "feedback error"));
    const button = node("button", loading ? "正在读取…" : "手动刷新");
    button.disabled = loading; button.addEventListener("click", () => loadQuote(model, true));
    model.card.append(button);
  }
  async function loadQuote(model, refresh = false) {
    const ticket = ++model.sequence;
    model.loading = true; model.error = ""; drawMarket(model);
    const value = {asset_id: model.asset.asset_id, metric: model.asset.metrics[0].metric};
    try {
      const result = refresh ? await api("/market/refresh", "POST", value) : await api(`/market/cache?${new URLSearchParams(value)}`);
      if (ticket === model.sequence) model.result = result;
    } catch (error) { if (ticket === model.sequence) model.error = error.message; }
    finally { if (ticket === model.sequence) { model.loading = false; drawMarket(model); } }
  }
  async function loadMarket() {
    say("market-status", "正在读取资产目录与缓存…");
    try {
      if (!state.market.size) {
        const assets = await api("/market/assets");
        $("market-list").replaceChildren();
        for (const asset of assets) {
          const model = {asset, card: node("article", undefined, "card market-card"), sequence: 0, result: null};
          state.market.set(asset.asset_id, model); $("market-list").append(model.card);
        }
      }
      await Promise.all([...state.market.values()].map((model) => loadQuote(model)));
      say("market-status", "已读取缓存；获取新记录请点击各项的手动刷新。");
    } catch (error) { say("market-status", error.message, "error"); }
  }
  $("market-reload").addEventListener("click", loadMarket);

  const tabs = [...document.querySelectorAll("[data-tab]")];
  function activateTab(button) {
    for (const tab of tabs) {
      const selected = tab === button;
      tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
      $(`panel-${tab.dataset.tab}`).hidden = !selected;
    }
    const name = button.dataset.tab;
    if (!state.loaded.has(name)) {
      state.loaded.add(name);
      if (name === "chat") loadConversations();
      if (name === "changes") loadChanges();
      if (name === "market") loadMarket();
    }
  }
  for (const tab of tabs) {
    tab.addEventListener("click", () => activateTab(tab));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const index = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (tabs.indexOf(tab) + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
      tabs[index].focus(); activateTab(tabs[index]);
    });
  }
  state.loaded.add("profile");
  loadProfile();
})();
