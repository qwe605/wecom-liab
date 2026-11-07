/** 合成演示及本地跳转回归；不启动浏览器、不访问企业微信或任何真实后端。 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { loginDestination } from "../src/api/navigation.js";

test("login destinations reject external, non-string and unauthorized next paths", () => {
  assert.equal(loginDestination("admin", "/records/"), "/records/");
  assert.equal(loginDestination("reviewer", "/records/"), "/review/");
  assert.equal(loginDestination("staff", "/records/"), "/sidebar/");
  for (const next of ["https://example.invalid", "//example.invalid", ["/users/"], "/%2f%2fexample.invalid", "/login/", null]) {
    assert.equal(loginDestination("admin", next), "/users/");
  }
});

for (const location of [{ pathname: "/sidebar-demo/", search: "" }, { pathname: "/sidebar/", search: "?demo=1" }]) {
  test(`synthetic demo never reads real conversations: ${location.pathname}${location.search}`, async () => {
    const source = readFileSync(new URL("../src/views/SidebarView.vue", import.meta.url), "utf8")
      .split("<script setup>")[1].split("</script>")[0].replace(/^import .*;$/gm, "");
    let requests = 0;
    const context = {
      ref: (value) => ({ value }),
      computed: (fn) => ({ get value() { return fn(); } }),
      onMounted: () => {}, onBeforeUnmount: () => {},
      location, URLSearchParams, console,
      token: () => "synthetic-existing-session",
      api: () => { requests++; throw new Error("Demo attempted a real backend request"); },
      window: { setInterval: () => { throw new Error("Demo attempted real conversation polling"); } },
    };
    vm.createContext(context);
    vm.runInContext(source, context);
    for (const call of ["initialize()", 'loadMessages("latest")', 'loadMessages("older")', "loadSummary()", "refreshCachedMessages()", "generate()", "generate(true)", "adopt()"]) {
      await vm.runInContext(call, context);
    }
    assert.equal(requests, 0);
  });
}

test("adoption only copies a draft and never invokes a message-sending SDK", async () => {
  const source = readFileSync(new URL("../src/api/wecom.js", import.meta.url), "utf8");
  assert.equal(source.includes("sendChatMessage"), false);
  const copied = [];
  const context = {
    navigator: { clipboard: { writeText: async (text) => copied.push(text) } },
    window: { wx: { invoke: () => { throw new Error("Sending API must never be invoked"); } } },
  };
  vm.createContext(context);
  vm.runInContext(source.replace(/^import .*;$/gm, "").replaceAll("export ", ""), context);
  await vm.runInContext('copyReplyDraft("合成草稿")', context);
  assert.deepEqual(copied, ["合成草稿"]);
  context.navigator = {};
  await assert.rejects(vm.runInContext('copyReplyDraft("合成草稿")', context), /不会代发消息/);
});
