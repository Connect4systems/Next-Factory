const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

function createForm() {
  let state = "Reposting";
  const timers = new Map();
  let nextTimer = 0;
  const messages = [
    { html: "Unrelated notice" },
    { html: "Production costing: Reposting" },
    { html: "Production costing: Reposting" },
  ];
  const frm = {
    doc: { docstatus: 1, name: "WO-TEST" },
    reloads: 0,
    introCalls: 0,
    layout: { message: { children: () => ({ filter: (fn) => ({
      remove: () => {
        const selected = messages.filter((node) => fn.call(node));
        selected.forEach((node) => messages.splice(messages.indexOf(node), 1));
      },
    }) }) } },
    remove_custom_button() {},
    add_custom_button() {},
    set_intro(html) { this.introCalls++; messages.push({ html }); },
    async reload_doc() { this.reloads++; },
  };
  const context = vm.createContext({
    console,
    cur_frm: frm,
    __: (text, args = []) => text.replace(/\{(\d+)\}/g, (_, index) => args[index]),
    $: (node) => ({
      find: () => ({ length: Number(node.html.includes("data-c4-production-cost-status")) }),
      text: () => node.html.replace(/<[^>]*>/g, ""),
    }),
    setTimeout: (callback, delay) => {
      timers.set(++nextTimer, { callback, delay });
      return nextTimer;
    },
    clearTimeout: (id) => timers.delete(id),
    frappe: {
      ui: { form: { on() {} } },
      utils: { escape_html: (text) => text },
      xcall: async () => ({ continuous_costing: true, cost_status: state, eligible: false }),
    },
  });
  vm.runInContext(fs.readFileSync(process.argv[2] || "c4factory/public/js/doctype/work_order.js", "utf8"), context);
  return {
    frm, messages, timers,
    configure: () => vm.runInContext("configure_finish_button(cur_frm)", context),
    setState: (value) => { state = value; },
    async tick(delay) {
      const pending = [...timers].filter(([, timer]) => timer.delay === delay);
      for (const [id, timer] of pending) {
        timers.delete(id);
        await timer.callback();
      }
    },
  };
}

test("one cost notice survives repeated button setup and preserves other notices", async () => {
  const page = createForm();
  await page.configure();
  await page.tick(300);
  await page.tick(1000);
  assert.equal(page.frm.introCalls, 1);
  assert.equal(page.messages.length, 2);
  assert.equal(page.messages.filter((node) => node.html.includes("Production costing:")).length, 1);
  assert.equal(page.messages[0].html, "Unrelated notice");
});

test("pending status polls without reloading; a changed status reloads once", async () => {
  const page = createForm();
  await page.configure();
  await page.tick(5000);
  await page.tick(5000);
  assert.equal(page.frm.reloads, 0);
  page.setState("Awaiting Timesheet Finish");
  await page.tick(5000);
  assert.equal(page.frm.reloads, 1);
  assert.equal([...page.timers.values()].filter((timer) => timer.delay === 5000).length, 0);
});
