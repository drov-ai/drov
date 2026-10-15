import { test, expect } from "@playwright/test";
import { durable, frames, png, verifiedBlob } from "../src/client";

function response(text: string, stride = 1) {
  const bytes = new TextEncoder().encode(text);
  return new Response(
    new ReadableStream({
      start(controller) {
        for (let i = 0; i < bytes.length; i += stride)
          controller.enqueue(bytes.slice(i, i + stride));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}

test("SSE split UTF-8/CRLF, multiline data, comment, transient IDs and incomplete tails", async () => {
  const source =
    ': keepalive\r\nid: run:1\r\nevent: run.running\r\ndata: {"durable":true,"run_id":"run",\r\ndata: "seq":1,"data":{"text":"中文"}}\r\n\r\nevent: token.delta\ndata: {"durable":false,"run_id":"run","data":"临时"}\n\nid: run:2\ndata: {"partial":true}';
  const items = [];
  for await (const item of frames(
    response(source),
    new AbortController().signal,
  ))
    items.push(item);
  expect(items).toHaveLength(2);
  expect(durable(items[0], "run")?.seq).toBe(1);
  expect(items[1].id).toBeNull();
  expect(durable(items[1], "run")).toBeNull();
  expect(durable(items[0], "other")).toBeNull();
  expect(() => durable({ ...items[0], id: "run:999" }, "run")).toThrow(
    "Invalid durable cursor",
  );
});

test("malformed or oversized event frames stop without acknowledging a cursor", async () => {
  const read = async (text: string) => {
    for await (const _ of frames(
      response(text, 65536),
      new AbortController().signal,
    )) {
      /* consume */
    }
  };
  await expect(read("id: run:1\ndata: invalid\n\n")).rejects.toThrow();
  await expect(read("data: " + "x".repeat(2 * 1024 * 1024))).rejects.toThrow(
    "display limit",
  );
});

test("artifact size and SHA-256 mismatch reject downloaded bytes", async () => {
  const bytes = new TextEncoder().encode("safe");
  const hash = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (x) => x.toString(16).padStart(2, "0"),
  ).join("");
  const info = {
    artifact_id: "art_" + "0".repeat(32),
    run_id: "r",
    state: "ready" as const,
    content_visible: true,
    name: "safe.txt",
    size_bytes: 4,
    media_type: "text/plain",
    sha256: hash,
  };
  expect(await (await verifiedBlob(new Response(bytes), info)).text()).toBe(
    "safe",
  );
  await expect(
    verifiedBlob(new Response(bytes), { ...info, sha256: "0".repeat(64) }),
  ).rejects.toThrow("校验失败");
  await expect(
    verifiedBlob(new Response(bytes), { ...info, size_bytes: 3 }),
  ).rejects.toThrow("大小不符");
  await expect(
    verifiedBlob(new Response(bytes), { ...info, size_bytes: 5 }),
  ).rejects.toThrow("未完成");
  await expect(
    verifiedBlob(new Response(bytes), {
      ...info,
      size_bytes: 17 * 1024 * 1024,
    }),
  ).rejects.toThrow("下载上限");
});

test("PNG header and pixel bomb are rejected before decoding", async () => {
  await expect(png(new Blob(["<svg></svg>"]))).rejects.toThrow("非有效 PNG");
  const data = new Uint8Array(33);
  data.set([137, 80, 78, 71, 13, 10, 26, 10]);
  const view = new DataView(data.buffer);
  view.setUint32(8, 13);
  view.setUint32(12, 0x49484452);
  view.setUint32(16, 100000);
  view.setUint32(20, 100000);
  await expect(png(new Blob([data]))).rejects.toThrow("像素预览上限");
});
