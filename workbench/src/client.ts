import type { components } from "./schema";
export type Wire = components["schemas"];
export type Event = Wire["DurableEvent"] | Wire["UnknownDurableEvent"];
export class HTTPError extends Error {
  constructor(
    public status: number,
    public code: string,
  ) {
    super(`${code} (${status})`);
  }
}

// The credential lives in this instance only; never in a URL, storage or logs.
export class Client {
  constructor(
    private credential: string,
    private denied: (status: number) => void,
  ) {}
  clear() {
    this.credential = "";
  }
  async fetch(path: string, options: RequestInit = {}) {
    if (!path.startsWith("/v1/")) throw new Error("Invalid API path");
    const headers = new Headers(options.headers);
    headers.set("Authorization", `Bearer ${this.credential}`);
    const response = await fetch(path, {
      ...options,
      headers,
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
    });
    if (!response.ok) {
      if ([401, 403, 404].includes(response.status))
        this.denied(response.status);
      let code = "request_failed";
      try {
        code = (await response.json()).error?.code || code;
      } catch {
        /* safe local message */
      }
      throw new HTTPError(response.status, code);
    }
    return response;
  }
  async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return (await this.fetch(path, { signal })).json();
  }
  async post<T>(path: string, body: unknown): Promise<T> {
    return (
      await this.fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
    ).json();
  }
}

export interface Frame {
  id: string | null;
  type: string;
  data: unknown;
}
export async function* frames(
  response: Response,
  signal: AbortSignal,
): AsyncGenerator<Frame> {
  if (
    !response.headers.get("content-type")?.startsWith("text/event-stream") ||
    !response.body
  )
    throw new Error("Invalid event stream");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "",
    kind = "",
    id: string | null = null,
    lines: string[] = [],
    size = 0;
  try {
    while (!signal.aborted) {
      const part = await reader.read();
      buffer += decoder.decode(part.value, { stream: !part.done });
      if (buffer.length + size > 2 * 1024 * 1024)
        throw new Error("Event frame exceeds display limit");
      let end: number;
      while ((end = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, end).replace(/\r$/, "");
        buffer = buffer.slice(end + 1);
        if (!line) {
          if (lines.length)
            yield {
              id,
              type: kind || "message",
              data: JSON.parse(lines.join("\n")),
            };
          kind = "";
          id = null;
          lines = [];
          size = 0;
        } else if (!line.startsWith(":")) {
          size += line.length;
          const colon = line.indexOf(":");
          const field = colon < 0 ? line : line.slice(0, colon);
          const value =
            colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
          if (field === "id" && !value.includes("\0")) id = value;
          if (field === "event") kind = value;
          if (field === "data") lines.push(value);
        }
      }
      if (part.done) break; // incomplete final frame is never acknowledged
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function durable(frame: Frame, run: string): Event | null {
  const data = frame.data as Event;
  if (
    !data ||
    data.durable !== true ||
    data.run_id !== run ||
    !Number.isSafeInteger(data.seq) ||
    data.seq < 1
  )
    return null;
  if (frame.id !== `${run}:${data.seq}`)
    throw new Error("Invalid durable cursor");
  return data;
}

export async function verifiedBlob(
  response: Response,
  info: Wire["ArtifactInfo"],
): Promise<Blob> {
  if (info.size_bytes > 16 * 1024 * 1024) {
    await response.body?.cancel();
    throw new Error("文件超过工作台 16 MiB 下载上限");
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("文件内容不可用");
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > info.size_bytes) throw new Error("文件大小不符");
      chunks.push(value);
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  if (size !== info.size_bytes) throw new Error("文件传输未完成");
  const blob = new Blob(chunks, { type: "application/octet-stream" });
  const digest = await crypto.subtle.digest(
    "SHA-256",
    await blob.arrayBuffer(),
  );
  const hash = Array.from(new Uint8Array(digest), (x) =>
    x.toString(16).padStart(2, "0"),
  ).join("");
  if (hash !== info.sha256) throw new Error("文件校验失败");
  return blob;
}

export async function png(blob: Blob): Promise<Blob> {
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer),
    view = new DataView(buffer);
  const magic = [137, 80, 78, 71, 13, 10, 26, 10];
  if (
    bytes.length < 33 ||
    !magic.every((x, i) => bytes[i] === x) ||
    view.getUint32(8) !== 13 ||
    view.getUint32(12) !== 0x49484452
  )
    throw new Error("非有效 PNG 文件");
  const width = view.getUint32(16),
    height = view.getUint32(20);
  if (!width || !height || width * height > 16_000_000)
    throw new Error("PNG 超过 16M 像素预览上限");
  const safe = new Blob([buffer], { type: "image/png" });
  const bitmap = await createImageBitmap(safe);
  try {
    if (bitmap.width !== width || bitmap.height !== height)
      throw new Error("PNG 尺寸不符");
  } finally {
    bitmap.close();
  }
  return safe;
}
