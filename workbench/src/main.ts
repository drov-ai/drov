import "./style.css";
import {
  Client,
  HTTPError,
  durable,
  frames,
  png,
  verifiedBlob,
  type Event,
  type Wire,
} from "./client";

type Snapshot = Wire["RunSnapshot"];
type Manifest = Wire["ContextManifest"];
const root = document.querySelector<HTMLDivElement>("#app")!;
const terminal = new Set(["completed", "failed", "cancelled"]);
const labels: Record<string, string> = {
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  suspended: "等待处理",
  running: "运行中",
  accepted: "已接收",
  approval: "审批",
  input: "补充输入",
  reconciliation: "核查",
  recovery: "待恢复",
};
const fmt = (value: unknown) =>
  typeof value === "string" ? value : JSON.stringify(value, null, 2);
const path = (value: string) => encodeURIComponent(value);
function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text = "",
  className = "",
) {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = className;
  return node;
}
function button(
  text: string,
  action: () => void | Promise<void>,
  className = "",
) {
  const node = el("button", text, className);
  node.type = "button";
  node.addEventListener("click", () => {
    void safe(action);
  });
  return node;
}
function field(label: string, input: HTMLElement) {
  const node = el("label", "", "field");
  node.append(el("span", label), input);
  return node;
}
function input(placeholder = "", value = "") {
  const node = el("input");
  node.placeholder = placeholder;
  node.value = value;
  return node;
}
function pre(value: unknown) {
  return el("pre", fmt(value) ?? "null");
}
function detail(title: string, value: unknown) {
  const box = el("details");
  box.append(el("summary", title), pre(value));
  return box;
}
let api: Client | null = null,
  content = false,
  epoch = 0;
let sessions: Wire["SessionSummary"][] = [],
  sessionCursor: string | null = null,
  sessionMore = false;
let runRefs: Wire["RunRef"][] = [],
  runCursor: string | null = null,
  runMore = false;
let selectedSession = "",
  selectedRun = "",
  snapshot: Snapshot | null = null;
let events = new Map<number, Event>(),
  manifests = new Map<string, Manifest>();
let catalog: Wire["AgentSummary"][] = [],
  activeTab = "overview",
  streamState = "未订阅",
  cursor: string | null = null;
let stream: AbortController | null = null,
  view = new AbortController(),
  retryTimer: ReturnType<typeof setTimeout> | null = null;
let transient = "",
  errorText = "",
  noticeText = "",
  retryCount = 0;
let staged: Wire["UploadReceipt"][] = [],
  uploadNames: string[] = [],
  startBody: Wire["StartBody"] | null = null;
let pendingResponse: {
  request_id: string;
  response: Wire["ResponseBody"]["response"];
} | null = null;
const urls = new Set<string>();
const drafts = new Map<string, Record<string, string>>();
const agentSelect = el("select");
agentSelect.setAttribute("aria-label", "Agent");
const sessionInput = input("会话名称", crypto.randomUUID());
sessionInput.maxLength = 128;
sessionInput.setAttribute("aria-label", "会话名称");
const prompt = el("textarea");
prompt.placeholder = "交给 Agent 一个任务…";
prompt.required = true;
prompt.setAttribute("aria-label", "任务");
const files = input();
files.type = "file";
files.multiple = true;
files.setAttribute("aria-label", "上传文件");
const notification = el("div", "", "notice");
notification.setAttribute("role", "status");
const sessionList = el("div"),
  history = el("div"),
  main = el("section"),
  side = el("aside", "", "inspector");
const connection = el("span", "", "connection");
async function safe(action: () => void | Promise<void>) {
  const identity = api;
  try {
    await action();
  } catch (error) {
    if (api !== identity) return;
    if (error instanceof DOMException && error.name === "AbortError") return;
    errorText =
      error instanceof HTTPError && [403, 404].includes(error.status)
        ? "访问权限不足或记录不可用，已清除当前正文。"
        : error instanceof HTTPError
          ? error.message
          : error instanceof Error
            ? error.message
            : "操作失败";
    notify();
  }
}
function notify() {
  notification.textContent = errorText || noticeText;
  notification.className = errorText ? "notice error" : "notice";
  notification.hidden = !notification.textContent;
}
function stop() {
  stream?.abort();
  stream = null;
  if (retryTimer) clearTimeout(retryTimer);
  retryTimer = null;
}
function purge() {
  epoch++;
  stop();
  view.abort();
  view = new AbortController();
  for (const url of urls) URL.revokeObjectURL(url);
  urls.clear();
  events.clear();
  manifests.clear();
  drafts.clear();
  snapshot = null;
  transient = "";
  cursor = null;
  pendingResponse = null;
  errorText = noticeText = "";
}
function denial(status: number) {
  purge();
  content = false;
  const toggle = document.querySelector<HTMLInputElement>(
    '[aria-label="显示正文"]',
  );
  if (toggle) toggle.checked = false;
  if (status === 401) {
    logout();
    return;
  }
  errorText = "访问权限不足或记录不可用，已清除当前正文。";
  render();
}
function logout() {
  purge();
  api?.clear();
  api = null;
  sessions = [];
  runRefs = [];
  sessionCursor = runCursor = null;
  sessionMore = runMore = false;
  catalog = [];
  staged = [];
  uploadNames = [];
  startBody = null;
  selectedRun = "";
  selectedSession = "";
  prompt.value = "";
  sessionInput.value = crypto.randomUUID();
  agentSelect.replaceChildren();
  files.value = "";
  files.disabled = false;
  content = false;
  activeTab = "overview";
  streamState = "未订阅";
  errorText = noticeText = "";
  for (const node of [sessionList, history, main, side, notification])
    node.replaceChildren();
  showLogin();
}
function showLogin() {
  root.replaceChildren();
  const screen = el("main", "", "login");
  const logo = el("img");
  logo.src = "/workbench/drov-icon.svg";
  logo.alt = "Drov";
  const form = el("form", "", "login-card");
  form.append(
    logo,
    el("span", "DROV / WORKBENCH", "eyebrow"),
    el("h1", "让每一步运行都清晰可见。"),
    el("p", "连接本地服务，查看上下文、处理交互并检查运行结果。", "muted"),
  );
  const credential = input("输入本次服务的访问令牌");
  credential.type = "password";
  credential.autocomplete = "off";
  credential.required = true;
  credential.maxLength = 8192;
  const submit = el("button", "连接工作台", "primary");
  const error = el("p", "", "login-error");
  error.setAttribute("role", "alert");
  form.append(
    field("访问令牌", credential),
    submit,
    error,
    el("p", "令牌仅保存在当前页面内存中。刷新页面后需重新连接。", "fine"),
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const loginEpoch = epoch;
    const value = credential.value.trim();
    credential.value = "";
    submit.disabled = true;
    const candidate = new Client(value, (status) => {
      if (api === candidate) denial(status);
    });
    void candidate
      .get<Wire["AgentCatalog"]>("/v1/agents")
      .then((result) => {
        if (loginEpoch !== epoch) {
          candidate.clear();
          return;
        }
        api = candidate;
        catalog = result.items;
        errorText = "";
        noticeText = "";
        shell();
        return loadSessions();
      })
      .catch(() => {
        candidate.clear();
        error.textContent = "连接失败，请检查令牌与服务状态。";
      })
      .finally(() => {
        submit.disabled = false;
      });
  });
  screen.append(form, el("span", "本地开发工作台 · 实验性", "login-foot"));
  root.append(screen);
}
function shell() {
  root.replaceChildren();
  const selection = agentSelect.value;
  agentSelect.replaceChildren();
  for (const a of catalog) {
    const option = el("option", `${a.name} · ${a.revision}`);
    option.value = JSON.stringify([a.name, a.revision]);
    agentSelect.append(option);
  }
  if (catalog.some((a) => JSON.stringify([a.name, a.revision]) === selection))
    agentSelect.value = selection;
  const app = el("div", "", "workspace"),
    nav = el("aside", "", "sidebar");
  const brand = el("div", "", "brand");
  const logo = el("img");
  logo.src = "/workbench/drov-icon.svg";
  logo.alt = "";
  brand.append(
    logo,
    el("strong", "drov"),
    el("span", "本地工作台", "brand-label"),
  );
  nav.append(
    brand,
    button(
      "＋  新任务",
      () => {
        purge();
        selectedRun = "";
        selectedSession = "";
        runRefs = [];
        staged = [];
        uploadNames = [];
        startBody = null;
        files.value = "";
        prompt.value = "";
        sessionInput.value = crypto.randomUUID();
        shell();
      },
      "new-task",
    ),
    el("div", "会话", "nav-label"),
    sessionList,
  );
  const foot = el("div", "", "nav-foot");
  foot.append(
    el("span", "●  同源连接", "live"),
    el("span", "实验性界面 · 0.1 alpha", "fine"),
    button("断开连接", logout, "quiet"),
  );
  nav.append(foot);
  const body = el("div", "", "body");
  const header = el("header");
  const title = el("div");
  title.append(el("span", "WORKSPACE", "eyebrow"), el("h1", "运行工作区"));
  const tools = el("div", "", "toolbar");
  const toggle = input();
  toggle.type = "checkbox";
  toggle.checked = content;
  toggle.setAttribute("aria-label", "显示正文");
  toggle.addEventListener("change", () => {
    const next = toggle.checked;
    purge();
    content = next;
    render();
    if (selectedRun) void safe(() => loadRun(selectedRun));
  });
  const caption = el("label", "", "content-toggle");
  caption.append(toggle, document.createTextNode("显示正文"));
  tools.append(
    caption,
    button(
      "刷新",
      async () => {
        const generation = epoch,
          session = selectedSession,
          run = selectedRun;
        await loadSessions();
        if (generation !== epoch) return;
        if (session) await loadSession(session);
        else if (run) await loadRun(run);
      },
      "quiet",
    ),
  );
  header.append(title, tools);
  const columns = el("div", "", "columns");
  columns.append(main, side);
  body.append(header, notification, columns);
  app.append(nav, body);
  root.append(app);
  agentSelect.disabled = staged.length > 0 || startBody !== null;
  sessionInput.disabled = staged.length > 0 || startBody !== null;
  render();
}
async function loadSessions(after?: string) {
  const generation = epoch,
    page = await api!.get<Wire["SessionPage"]>(
      "/v1/sessions?limit=50" + (after ? `&after=${path(after)}` : ""),
      view.signal,
    );
  if (generation !== epoch) return;
  sessions = after ? [...sessions, ...page.items] : page.items;
  sessionCursor = page.next_cursor ?? null;
  sessionMore = page.has_more;
  renderSessions();
}
function renderSessions() {
  sessionList.replaceChildren();
  if (!sessions.length)
    sessionList.append(el("p", "任务开始后，会话会出现在这里。", "empty-nav"));
  for (const session of sessions) {
    const row = button(
      "",
      () => loadSession(session.session_id),
      "session" + (selectedSession === session.session_id ? " selected" : ""),
    );
    row.append(
      el("span", session.session_id, "session-name"),
      el("small", session.agent_name),
    );
    sessionList.append(row);
  }
  if (sessionMore && sessionCursor)
    sessionList.append(
      button("更多会话", () => loadSessions(sessionCursor!), "quiet"),
    );
}
async function loadSession(id: string, after?: string) {
  const generation = epoch;
  const page = await api!.get<Wire["SessionView"]>(
    `/v1/session?session_id=${path(id)}&limit=50${after ? "&after=" + path(after) : ""}`,
    view.signal,
  );
  if (generation !== epoch) return;
  selectedSession = id;
  runRefs = after ? [...runRefs, ...page.runs] : page.runs;
  runCursor = page.next_cursor ?? null;
  runMore = page.has_more;
  sessionInput.value = id;
  renderSessions();
  renderHistory();
  if (!after && page.runs.length) await loadRun(page.runs[0].run_id);
}
function renderHistory() {
  history.replaceChildren();
  if (runRefs.length) history.append(el("span", "会话运行", "section-label"));
  for (const ref of runRefs)
    history.append(
      button(
        ref.run_id.slice(0, 8),
        () => loadRun(ref.run_id),
        selectedRun === ref.run_id ? "run-chip selected" : "run-chip",
      ),
    );
  if (runMore && runCursor)
    history.append(
      button(
        "更多运行",
        () => loadSession(selectedSession, runCursor!),
        "quiet",
      ),
    );
}
function render() {
  if (!api) return;
  const focused = document.activeElement as
    | HTMLTextAreaElement
    | HTMLSelectElement
    | null;
  const focusName = focused?.dataset.draft;
  const selection =
    focused instanceof HTMLTextAreaElement
      ? [focused.selectionStart, focused.selectionEnd]
      : null;
  notify();
  renderSessions();
  main.replaceChildren();
  side.replaceChildren();
  renderHistory();
  main.className = "main";
  if (!selectedRun) {
    renderComposer();
    renderSide();
    return;
  }
  main.append(history);
  const heading = el("div", "", "run-heading");
  heading.append(
    el("h2", snapshot ? `${snapshot.agent_name}` : "读取运行…"),
    connection,
  );
  connection.textContent = streamState;
  main.append(heading);
  const tabs = el("div", "", "tabs");
  tabs.setAttribute("role", "tablist");
  for (const [id, title] of [
    ["overview", "概览"],
    ["context", "上下文"],
    ["events", "事件"],
    ["files", "文件"],
  ]) {
    const tab = button(
      title,
      () => {
        activeTab = id;
        render();
      },
      activeTab === id ? "tab active" : "tab",
    );
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(activeTab === id));
    tabs.append(tab);
  }
  main.append(tabs);
  const panel = el("div", "", "panel");
  panel.setAttribute("role", "tabpanel");
  if (!snapshot) panel.append(el("p", "读取运行状态…", "muted"));
  else if (activeTab === "overview") renderOverview(panel);
  else if (activeTab === "events") renderEvents(panel);
  else if (activeTab === "context") renderContexts(panel);
  else renderFiles(panel);
  main.append(panel);
  renderSide();
  if (focusName) {
    const restored = main.querySelector<
      HTMLTextAreaElement | HTMLSelectElement
    >(`[data-draft="${focusName}"]`);
    restored?.focus({ preventScroll: true });
    if (selection && restored instanceof HTMLTextAreaElement)
      restored.setSelectionRange(selection[0], selection[1]);
  }
}
function renderComposer() {
  agentSelect.disabled = staged.length > 0 || startBody !== null;
  sessionInput.disabled = staged.length > 0 || startBody !== null;
  const intro = el("div", "", "intro");
  intro.append(
    el("span", "从一个任务开始", "eyebrow"),
    el("h2", "定义目标，\n看见执行的每一步。"),
    el(
      "p",
      "选择已注册的 Agent。所有运行、权限与交互都由当前服务处理。",
      "muted",
    ),
  );
  const cards = el("div", "", "sample-cards");
  for (const [name, title, note, text] of [
    ["research", "研究与检索", "工具与来源", "比较两种缓存方案"],
    ["assistant", "个人助理", "记忆与上下文", "记住我的表达偏好"],
    ["operations", "事项处理", "输入与审批", "选择事项并提交处理"],
  ]) {
    if (!catalog.some((a) => a.name === name)) continue;
    const card = button(
      "",
      () => {
        const a = catalog.find((a) => a.name === name)!;
        agentSelect.value = JSON.stringify([a.name, a.revision]);
        prompt.value = text;
        prompt.focus();
      },
      "sample",
    );
    card.append(el("span", title), el("small", note));
    cards.append(card);
  }
  const form = el("form", "", "composer");
  const row = el("div", "", "form-row");
  row.append(field("Agent / 版本", agentSelect), field("会话", sessionInput));
  const uploaded = el(
    "p",
    uploadNames.length
      ? `已暂存：${uploadNames.join("、")}。暂存不等于已接收。`
      : "可选文件 · 单个不超过 8 MiB",
    "fine",
  );
  const submit = el(
    "button",
    startBody ? "重试同一提交" : "开始运行 ↗",
    "primary",
  );
  submit.disabled = !catalog.length;
  const upload = button(
    "暂存文件",
    async () => {
      if (!files.files?.length) throw new Error("请先选择文件");
      if (files.files.length + staged.length > 8)
        throw new Error("最多 8 个输入文件");
      const [name, revision] = JSON.parse(agentSelect.value) as string[];
      const generation = epoch,
        client = api!;
      const session = sessionInput.value;
      upload.disabled = true;
      agentSelect.disabled = sessionInput.disabled = files.disabled = true;
      try {
        for (const file of files.files) {
          if (file.size > 8 * 1024 * 1024)
            throw new Error("文件超过工作台 8 MiB 上传上限");
          const query = new URLSearchParams({
            agent_name: name,
            revision,
            session_id: session,
            name: file.name,
            media_type: /^[\w.+-]+\/[\w.+-]+$/.test(file.type)
              ? file.type
              : "application/octet-stream",
          });
          const response = await client.fetch("/v1/artifacts?" + query, {
            method: "POST",
            headers: { "Content-Type": "application/octet-stream" },
            body: file,
          });
          const receipt = await response.json();
          if (generation !== epoch) return;
          staged.push(receipt);
          uploadNames.push(file.name);
        }
        files.value = "";
      } finally {
        upload.disabled = files.disabled = false;
        if (generation === epoch) shell();
      }
    },
    "quiet",
  );
  upload.disabled = startBody !== null;
  const actions = el("div", "", "composer-actions");
  actions.append(files, upload, submit);
  form.append(row, field("任务", prompt), uploaded, actions);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit.disabled = true;
    void safe(startRun).finally(() => {
      submit.disabled = false;
    });
  });
  intro.append(cards, form);
  main.append(intro);
}
async function startRun() {
  errorText = "";
  noticeText = "";
  notify();
  const generation = epoch,
    client = api!;
  if (!startBody) {
    const [agent_name, revision] = JSON.parse(agentSelect.value) as string[];
    startBody = {
      agent_name,
      revision,
      session_id: sessionInput.value,
      input: prompt.value,
      request_key: crypto.randomUUID(),
      upload_ids: staged.map((x) => x.upload_id),
    };
  }
  const submission = startBody;
  // A lost response is retried with exactly the saved body and request key.
  agentSelect.disabled = true;
  sessionInput.disabled = true;
  prompt.disabled = true;
  try {
    const ref = await client.post<Wire["RunRef"]>("/v1/runs", submission);
    if (generation !== epoch || api !== client) return;
    selectedSession = submission.session_id;
    staged = [];
    uploadNames = [];
    startBody = null;
    await loadSessions();
    await loadSession(selectedSession);
    if (selectedRun !== ref.run_id) await loadRun(ref.run_id);
  } catch (error) {
    if (generation !== epoch || api !== client) return;
    if (error instanceof HTTPError && error.status < 500) startBody = null;
    render();
    throw error;
  } finally {
    prompt.disabled = false;
  }
}
async function reloadNotice(run: string, text: string) {
  const identity = api;
  const loading = loadRun(run),
    generation = epoch;
  await loading;
  if (generation === epoch && api === identity) {
    noticeText = text;
    notify();
  }
}
async function loadRun(id: string) {
  purge();
  selectedRun = id;
  errorText = "";
  streamState = "正在连接";
  render();
  const generation = epoch;
  const page = await api!.get<Wire["RunInspection"]>(
    `/v1/runs/${path(id)}?limit=100&include_content=${content}`,
    view.signal,
  );
  if (generation !== epoch) return;
  snapshot = page.snapshot;
  for (const event of page.events) accept(event);
  for (const manifest of page.context_manifests ?? [])
    manifests.set(manifest.model_call_id, manifest);
  render();
  retryCount = 0;
  void subscribe(generation);
}
function accept(event: Event) {
  if (event.run_id !== selectedRun) return;
  events.set(event.seq, event);
  if (!cursor || event.seq > Number(cursor.slice(cursor.lastIndexOf(":") + 1)))
    cursor = `${selectedRun}:${event.seq}`;
  if (events.size > 1000) events.delete(Math.min(...events.keys()));
  if (event.type === "context.prepared" && event.data.context_manifest) {
    const m = event.data.context_manifest as Manifest;
    manifests.set(m.model_call_id, m);
  }
}
async function refreshSnapshot(generation: number) {
  const page = await api!.get<Wire["RunInspection"]>(
    `/v1/runs/${path(selectedRun)}?limit=1&include_content=${content}`,
    view.signal,
  );
  if (generation !== epoch) return;
  snapshot = page.snapshot;
  render();
}
async function subscribe(generation: number) {
  if (generation !== epoch || !api || !selectedRun) return;
  stop();
  const controller = new AbortController();
  stream = controller;
  streamState = "实时连接";
  render();
  try {
    const response = await api.fetch(
      `/v1/runs/${path(selectedRun)}/events?include_content=${content}${cursor ? "&after=" + path(cursor) : ""}`,
      { signal: controller.signal },
    );
    for await (const frame of frames(response, controller.signal)) {
      if (generation !== epoch || controller.signal.aborted) return;
      if (frame.type === "stream.error") {
        const code =
          (frame.data as { data?: { code?: string } }).data?.code ||
          "stream_unavailable";
        if (
          ["permission_denied", "record_not_found", "unauthenticated"].includes(
            code,
          )
        ) {
          denial(code === "unauthenticated" ? 401 : 403);
          return;
        }
        await refreshSnapshot(generation);
        if (generation === epoch) {
          streamState = "订阅已结束";
          errorText = `事件订阅结束：${code}`;
          render();
        }
        return;
      }
      if (frame.type === "stream.gap") {
        transient = "";
        noticeText = "临时流存在缺口；持久事件会从游标补读。";
        continue;
      }
      const event = durable(frame, selectedRun);
      if (event) {
        accept(event);
        render();
      } else if (
        frame.type === "token.delta" &&
        content &&
        typeof (frame.data as { data?: unknown }).data === "string"
      ) {
        transient = (transient + (frame.data as { data: string }).data).slice(
          -65536,
        );
        render();
      }
    }
    if (generation !== epoch || controller.signal.aborted) return;
    await refreshSnapshot(generation);
    if (generation !== epoch) return;
    if (
      snapshot &&
      (terminal.has(snapshot.status) || snapshot.status === "suspended")
    ) {
      streamState = "已同步";
      render();
      return;
    }
    throw new Error("连接已断开");
  } catch (error) {
    if (generation !== epoch || controller.signal.aborted || !api) return;
    if (error instanceof HTTPError && [400, 410].includes(error.status)) {
      errorText = "事件游标已失效，请刷新运行快照。";
      streamState = "需要刷新";
      render();
      return;
    }
    streamState = "连接中断，等待重连";
    noticeText = "重连只补读事件；不会重新提交任务。";
    render();
    retryTimer = setTimeout(
      () => {
        void subscribe(generation);
      },
      Math.min(10000, 500 * 2 ** retryCount++),
    );
  }
}
function renderOverview(panel: HTMLElement) {
  if (!snapshot) return;
  const status = el("div", "", "status-card");
  status.append(
    el(
      "span",
      labels[snapshot.status] || snapshot.status,
      "status " + snapshot.status,
    ),
    el("span", `${snapshot.agent_name} · ${snapshot.revision}`, "muted"),
  );
  panel.append(status);
  if (!content)
    panel.append(
      el(
        "p",
        "当前仅显示元数据。打开「显示正文」以读取输出、工具参数和交互内容。",
        "restricted",
      ),
    );
  if (snapshot.outcome?.status === "completed") {
    const result = el("section", "", "answer");
    result.append(
      el("span", "最终输出", "section-label"),
      pre(snapshot.outcome.output),
    );
    panel.append(result);
  }
  if (snapshot.outcome?.status === "failed")
    panel.append(detail("运行错误", snapshot.outcome.error));
  if (transient && snapshot.status === "running") {
    const text = el("section", "", "answer");
    text.append(
      el("span", "临时输出 · 重连后可能不完整", "section-label"),
      pre(transient),
    );
    panel.append(text);
  }
  if (snapshot.pending && content) renderInteraction(panel);
  for (const operation of snapshot.operations ?? []) {
    const card = el("section", "", "operation");
    card.append(
      el("h3", operation.tool_name),
      el("span", `${operation.status} · ${operation.effect}`, "muted"),
    );
    const own = [...events.values()].filter(
      (e) => e.data.operation_id === operation.operation_id,
    );
    const args = own.find((e) => e.data.arguments !== undefined)?.data
      .arguments;
    const result = own.find((e) => e.type === "tool.succeeded")?.data.result;
    if (content && args !== undefined) {
      card.append(detail("工具参数 / 代码与输入清单", args));
      if (
        operation.tool_name === "sandbox.run_python" &&
        args &&
        typeof args === "object" &&
        "code" in args
      )
        card.append(detail("代码", args.code));
    }
    if (content && result !== undefined) {
      if (
        operation.tool_name === "sandbox.run_python" &&
        typeof result === "object" &&
        result &&
        "execution_key" in result
      ) {
        const value = result as Record<string, unknown>;
        card.append(
          el(
            "p",
            `退出：${value.exit_code ?? "无退出码"} · ${value.termination} · 清理：已确认销毁`,
            "execution-fact",
          ),
          detail("有界标准输出 / 错误与文件清单", value),
        );
      } else card.append(detail("工具结果", result));
    } else if (operation.tool_name === "sandbox.run_python")
      card.append(
        el("p", "清理：尚无已发布的销毁证据，请检查原运行状态。", "fine"),
      );
    panel.append(card);
  }
}
function renderInteraction(panel: HTMLElement) {
  const pending = snapshot?.pending;
  if (!pending || !("prompt" in pending)) return;
  const form = el("form", "", "interaction");
  form.append(
    el("span", "需要你的处理", "eyebrow"),
    el("h3", labels[pending.kind]),
    el("p", pending.prompt),
    el("p", `有效期至 ${pending.expires_at}`, "fine"),
  );
  const requestId = pending.request_id;
  const draft = drafts.get(requestId) || {};
  drafts.set(requestId, draft);
  const keep = (
    name: string,
    control: HTMLTextAreaElement | HTMLSelectElement,
  ) => {
    control.dataset.draft = name;
    control.value = draft[name] ?? control.value;
    control.addEventListener("input", () => {
      draft[name] = control.value;
    });
  };
  if ("arguments_summary" in pending)
    form.append(detail("原计划参数", pending.arguments_summary));
  const actions = el("div", "", "interaction-actions");
  const send = async (response: Wire["ResponseBody"]["response"]) => {
    const generation = epoch,
      run = selectedRun,
      client = api!;
    for (const control of form.querySelectorAll<HTMLButtonElement>("button"))
      control.disabled = true;
    const body =
      pendingResponse?.request_id === requestId
        ? pendingResponse
        : { request_id: requestId, response };
    pendingResponse = body;
    try {
      const receipt = await client.post<Wire["ResponseReceipt"]>(
        `/v1/runs/${path(run)}/responses`,
        body,
      );
      if (generation !== epoch) return;
      pendingResponse = null;
      await reloadNotice(run, `响应已接收 · ${receipt.response_id}`);
    } catch (error) {
      if (error instanceof HTTPError && error.status < 500)
        pendingResponse = null;
      if (generation === epoch) render();
      throw error;
    } finally {
      for (const control of form.querySelectorAll<HTMLButtonElement>("button"))
        control.disabled = false;
    }
  };
  if (pendingResponse?.request_id === requestId) {
    actions.append(
      button("重试原响应", () => send(pendingResponse!.response), "primary"),
    );
  } else if (pending.kind === "approval") {
    actions.append(
      button(
        "批准原计划",
        () => send({ kind: "approval", approved: true }),
        "primary",
      ),
      button(
        "拒绝",
        () => send({ kind: "approval", approved: false }),
        "danger",
      ),
    );
  } else if (pending.kind === "input" && "input_schema" in pending) {
    const value = el("textarea");
    value.required = true;
    value.setAttribute("aria-label", "补充输入");
    keep("input", value);
    form.append(
      detail("输入格式", pending.input_schema),
      field(pending.input_schema.type === "string" ? "内容" : "JSON 值", value),
    );
    const submit = el("button", "提交输入", "primary");
    actions.append(submit);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      void safe(() =>
        send({
          kind: "input",
          value:
            pending.input_schema.type === "string"
              ? value.value
              : JSON.parse(value.value),
        }),
      );
    });
  } else if (pending.kind === "reconciliation") {
    if ("explanation" in pending) form.append(el("p", pending.explanation));
    const resolution = el("select");
    resolution.setAttribute("aria-label", "核查结论");
    for (const [value, label] of [
      ["unresolved", "仍未确定"],
      ["applied", "已经生效"],
      ["not_applied", "确认未生效"],
    ]) {
      const option = el("option", label);
      option.value = value;
      resolution.append(option);
    }
    keep("resolution", resolution);
    const evidence = el("textarea");
    keep("evidence", evidence);
    evidence.required = true;
    evidence.setAttribute("aria-label", "核查依据");
    const result = el("textarea");
    keep("result", result);
    result.setAttribute("aria-label", "核查结果 JSON");
    result.placeholder = "可选：已生效时提供工具结果 JSON";
    const submit = el("button", "提交核查", "primary");
    form.append(
      field("结论", resolution),
      field("依据", evidence),
      field("结果 JSON（仅已生效）", result),
    );
    actions.append(
      button(
        "调用原核查器",
        async () => {
          const generation = epoch,
            run = selectedRun,
            client = api!;
          const decision = await client.post<
            Extract<
              Wire["ResponseBody"]["response"],
              { kind: "reconciliation" }
            >
          >(`/v1/runs/${path(run)}/verify`, { request_id: requestId });
          if (generation !== epoch) return;
          await reloadNotice(run, "核查器返回：" + fmt(decision));
        },
        "quiet",
      ),
      submit,
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      void safe(async () => {
        const response: Extract<
          Wire["ResponseBody"]["response"],
          { kind: "reconciliation" }
        > = {
          kind: "reconciliation",
          resolution: resolution.value as
            | "applied"
            | "not_applied"
            | "unresolved",
          evidence: evidence.value,
        };
        if (result.value.trim() && resolution.value === "applied")
          response.result = JSON.parse(result.value);
        await send(response);
      });
    });
  }
  form.append(actions);
  panel.append(form);
}
function renderEvents(panel: HTMLElement) {
  panel.append(
    el(
      "p",
      "持久事件按序号合并，最多保留最近 1000 项；临时 token 不作为重放游标。",
      "fine",
    ),
  );
  for (const event of [...events.values()].sort((a, b) => a.seq - b.seq)) {
    const row = el("details", "", "event");
    row.dataset.seq = String(event.seq);
    row.append(
      el("summary", `${String(event.seq).padStart(3, "0")}  ${event.type}`),
      pre(event.data),
    );
    panel.append(row);
  }
}
function renderContexts(panel: HTMLElement) {
  if (!manifests.size) panel.append(el("p", "尚无上下文记录。", "muted"));
  for (const [id, manifest] of manifests) {
    const card = el("section", "", "context-card");
    card.append(
      el("h3", "模型输入 · " + id.slice(0, 8)),
      el(
        "p",
        `预算 ${manifest.budget} · 估算 ${manifest.estimated_tokens} · ${manifest.estimator} · 检索 ${manifest.retrieval_status}`,
        "fine",
      ),
    );
    card.append(
      button(
        "读取来源清单",
        async () => {
          const generation = epoch;
          const full = await api!.get<Manifest>(
            `/v1/runs/${path(selectedRun)}/contexts/${path(id)}?include_content=${content}`,
            view.signal,
          );
          if (generation === epoch) {
            manifests.set(id, full);
            render();
          }
        },
        "quiet",
      ),
    );
    for (const source of manifest.sources) {
      const row = el("div", "", "source");
      row.append(
        el("strong", `${source.included ? "选入" : "排除"} · ${source.kind}`),
        el(
          "span",
          `${source.reason} · ${source.transform} · ~${source.estimated_tokens} tokens`,
          "fine",
        ),
      );
      if (content && source.kind !== "provider" && source.content !== null)
        row.append(detail("来源内容", source.content));
      card.append(row);
    }
    card.append(detail("已报告用量", manifest.reported_usage));
    panel.append(card);
  }
}
function artifactIds() {
  const ids = new Set<string>();
  for (const manifest of manifests.values())
    for (const source of manifest.sources)
      if (source.kind === "artifact" && source.id.startsWith("artifact:"))
        ids.add(source.id.slice(9));
  // Only the protocol's explicit artifact references, never arbitrary strings in output.
  for (const e of events.values()) {
    if (!e.content_visible) continue;
    if (e.type === "run.accepted" && Array.isArray(e.data.artifacts))
      for (const ref of e.data.artifacts) {
        if (
          ref &&
          typeof ref === "object" &&
          "artifact_id" in ref &&
          typeof ref.artifact_id === "string"
        )
          ids.add(ref.artifact_id);
      }
    if (
      e.type === "tool.prepared" &&
      e.data.arguments &&
      typeof e.data.arguments === "object" &&
      "inputs" in e.data.arguments &&
      Array.isArray(e.data.arguments.inputs)
    )
      for (const id of e.data.arguments.inputs)
        if (typeof id === "string") ids.add(id);
    if (e.type === "tool.succeeded") {
      const result = e.data.result;
      if (
        result &&
        typeof result === "object" &&
        "artifacts" in result &&
        Array.isArray(result.artifacts)
      )
        for (const ref of result.artifacts) {
          if (
            ref &&
            typeof ref === "object" &&
            "artifact_id" in ref &&
            typeof ref.artifact_id === "string"
          )
            ids.add(ref.artifact_id);
        }
    }
  }
  return [...ids].filter((id) => /^art_[0-9a-f]{32}$/.test(id));
}
function renderFiles(panel: HTMLElement) {
  if (!content) {
    panel.append(el("p", "文件名与预览需要正文权限。", "restricted"));
    return;
  }
  panel.append(
    el(
      "p",
      "仅 PNG 可预览。其他文件作为附件下载；读取会重新检查授权与 SHA-256。",
      "fine",
    ),
  );
  panel.append(
    button(
      "读取输入清单",
      async () => {
        const generation = epoch;
        for (const id of manifests.keys()) {
          const full = await api!.get<Manifest>(
            `/v1/runs/${path(selectedRun)}/contexts/${path(id)}?include_content=${content}`,
            view.signal,
          );
          if (generation !== epoch) return;
          manifests.set(id, full);
        }
        render();
      },
      "quiet",
    ),
  );
  const ids = artifactIds();
  if (!ids.length)
    panel.append(el("p", "当前已读取事件中没有文件引用。", "muted"));
  for (const id of ids) {
    const card = el("section", "", "file-card");
    card.append(el("code", id));
    card.append(
      button(
        "读取文件信息",
        async () => {
          const generation = epoch,
            client = api!,
            signal = view.signal;
          const info = await client.get<Wire["ArtifactInfo"]>(
            `/v1/artifacts/${path(id)}?include_content=true`,
            signal,
          );
          if (generation !== epoch || !info.content_visible || !content) return;
          card.replaceChildren(
            el("h3", info.name!),
            el(
              "p",
              `${info.media_type} · ${info.size_bytes} bytes · ${info.state}`,
              "fine",
            ),
            el("code", info.sha256),
          );
          if (info.state !== "ready") return;
          const obtain = async () => {
            const current = await client.get<Wire["ArtifactInfo"]>(
              `/v1/artifacts/${path(id)}?include_content=true`,
              signal,
            );
            const data = await verifiedBlob(
              await client.fetch(`/v1/artifacts/${path(id)}/content`, {
                signal: signal,
              }),
              current,
            );
            // Recheck after download: revocation during the transfer cannot leave a preview.
            await client.get<Wire["ArtifactInfo"]>(
              `/v1/artifacts/${path(id)}?include_content=true`,
              signal,
            );
            if (generation !== epoch || !content)
              throw new DOMException("Stale view", "AbortError");
            return data;
          };
          card.append(
            button(
              "下载附件",
              async () => {
                const blob = await obtain();
                const url = URL.createObjectURL(blob);
                urls.add(url);
                const link = el("a");
                link.href = url;
                link.download = info.name || "artifact";
                link.click();
                setTimeout(() => {
                  URL.revokeObjectURL(url);
                  urls.delete(url);
                }, 1000);
              },
              "quiet",
            ),
          );
          if (info.media_type === "image/png")
            card.append(
              button(
                "预览 PNG",
                async () => {
                  const blob = await png(await obtain());
                  if (generation !== epoch || !content) return;
                  const url = URL.createObjectURL(blob);
                  urls.add(url);
                  const image = el("img");
                  image.src = url;
                  image.alt = info.name || "PNG 产物";
                  image.className = "preview";
                  const old = card.querySelector<HTMLImageElement>(".preview");
                  if (old) {
                    URL.revokeObjectURL(old.src);
                    urls.delete(old.src);
                    old.remove();
                  }
                  card.append(image);
                },
                "quiet",
              ),
            );
        },
        "quiet",
      ),
    );
    panel.append(card);
  }
}
function renderSide() {
  side.append(el("span", "RUN INSPECTOR", "eyebrow"), el("h2", "运行检查"));
  if (!snapshot) {
    side.append(
      el("p", "开始或选择一个运行，检查它的状态、用量与操作。", "muted"),
      el("div", "每个入口，共用同一个运行内核。", "inspector-note"),
    );
    return;
  }
  const list = el("dl");
  for (const [key, value] of [
    ["状态", labels[snapshot.status] || snapshot.status],
    ["Run ID", snapshot.run_id],
    ["会话", snapshot.session_id],
    ["状态版本", snapshot.state_version],
    [
      "暂停原因",
      snapshot.reason ? labels[snapshot.reason] || snapshot.reason : "—",
    ],
    ["取消请求", snapshot.cancel_requested ? "已提交" : "未提交"],
    ["创建时间", snapshot.created_at],
    ["更新时间", snapshot.updated_at],
  ])
    list.append(el("dt", key as string), el("dd", String(value)));
  side.append(list, detail("已知用量", snapshot.usage));
  side.append(
    button(
      "重连事件",
      async () => {
        stop();
        retryCount = 0;
        const generation = epoch;
        await refreshSnapshot(generation);
        if (generation === epoch) void subscribe(generation);
      },
      "quiet full",
    ),
  );
  if (!terminal.has(snapshot.status))
    side.append(
      button(
        "取消运行",
        async () => {
          const generation = epoch,
            run = selectedRun,
            client = api!;
          const receipt = await client.post<Wire["CancelReceipt"]>(
            `/v1/runs/${path(run)}/cancel`,
            {},
          );
          if (generation !== epoch) return;
          await reloadNotice(
            run,
            receipt.cancel_requested
              ? "取消请求已提交，等待内核确认。"
              : labels[receipt.status],
          );
        },
        "danger full",
      ),
    );
  if (snapshot.recoverable)
    side.append(
      button(
        "显式恢复原运行",
        async () => {
          const generation = epoch,
            run = selectedRun,
            client = api!;
          await client.post(`/v1/runs/${path(run)}/recover`, {});
          if (generation === epoch) await loadRun(run);
        },
        "quiet full",
      ),
    );
  side.append(
    el("p", "断开页面与事件订阅不会取消运行。取消和恢复需要显式操作。", "fine"),
  );
}
window.addEventListener("pagehide", logout);
showLogin();
