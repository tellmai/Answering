"use strict";
let paper;
let submitted = false;
let clockTimer;
const byId = id => document.getElementById(id);
const notice = message => { byId("submitNotice").textContent = message; };
function element(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}
function readAnswers() {
  return Object.fromEntries([...document.querySelectorAll("article.question")].map(card =>
    [card.dataset.questionId, [...card.querySelectorAll("input:checked")].map(input => input.value)]));
}
function updateProgress() {
  const answers = readAnswers();
  const done = Object.values(answers).filter(values => values.length).length;
  byId("answerProgress").textContent = `已答 ${done} / ${paper.total} 题`;
  byId("completion").value = done;
  for (const [id, values] of Object.entries(answers)) byId(`nav-${id}`).classList.toggle("answered", values.length > 0);
}
function render(data) {
  paper = data;
  const started = performance.now();
  byId("timingMode").textContent = data.plannedSeconds ? `计划用时 ${(data.plannedSeconds / 60).toFixed(1)} 分钟` : "最快完成 · 仍校验答案";
  byId("scorePolicy").textContent = data.scoreRange ? `模拟分数范围 ${data.scoreRange[0]}–${data.scoreRange[1]} 分 · 最低 ${data.minimumScore} 分` : "每题 1 分 · 满分 100 分";
  clockTimer = setInterval(() => {
    const seconds = Math.floor((performance.now() - started) / 1000);
    byId("elapsedTime").textContent = `已用 ${String(Math.floor(seconds / 60)).padStart(2,"0")}:${String(seconds % 60).padStart(2,"0")}`;
  }, 1000);
  byId("candidateName").textContent = data.name;
  byId("candidateDistrict").textContent = data.district;
  byId("candidateId").textContent = data.maskedId;
  byId("typeCounts").textContent = ["单选", "多选", "判断"].map(kind => `${kind} ${data.counts[kind]} 题`).join(" · ");
  byId("questions").replaceChildren();
  for (const [index, q] of data.questions.entries()) {
    const card = element("article", "question card");
    card.id = `question-${q.id}`;
    card.dataset.questionId = q.id;
    card.dataset.kind = q.kind;
    const top = element("div", "question-top");
    const title = element("div", "question-number", `第 ${index + 1} 题`);
    title.append(element("span", "kind-tag", `${q.kind}题`));
    top.append(title, element("span", "points", "1 分"));
    card.append(top, element("p", "stem", q.stem));
    const options = element("div", "options");
    for (const [letter, text] of Object.entries(q.options)) {
      const label = element("label", "option");
      const input = element("input");
      input.type = q.kind === "多选" ? "checkbox" : "radio";
      input.name = `q-${q.id}`;
      input.value = letter;
      input.addEventListener("change", updateProgress);
      label.append(input, element("span", "option-label", `${letter}.`), element("span", "option-text", text));
      options.append(label);
    }
    card.append(options);
    byId("questions").append(card);
    const nav = element("button", "", String(index + 1));
    nav.id = `nav-${q.id}`;
    nav.type = "button";
    nav.setAttribute("aria-label", `跳转到第 ${index + 1} 题`);
    nav.addEventListener("click", () => card.scrollIntoView({behavior:"smooth", block:"start"}));
    byId("answerGrid").append(nav);
  }
  byId("submitExam").disabled = false;
}
byId("submitExam").addEventListener("click", async () => {
  if (submitted) return;
  const answers = readAnswers();
  const remaining = Object.values(answers).filter(values => !values.length).length;
  if (remaining) { notice(`还有 ${remaining} 题未作答，请完成后提交。`); return; }
  byId("submitExam").disabled = true;
  notice("正在提交并评分…");
  try {
    const response = await fetch("submit", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(answers)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "提交失败，请重试");
    submitted = true;
    clearInterval(clockTimer);
    for (const input of document.querySelectorAll(".question input")) input.disabled = true;
    for (const item of result.details) {
      const card = byId(`question-${item.id}`);
      card.append(element("div", `answer-feedback${item.correct ? "" : " wrong"}`, `${item.correct ? "回答正确" : "回答错误"} · 你的答案：${item.selected.join("、") || "未作答"} · 正确答案：${item.answers.join("、")}`));
      byId(`nav-${item.id}`).classList.toggle("wrong", !item.correct);
    }
    byId("scoreValue").textContent = String(result.score);
    byId("resultSummary").textContent = `${paper.name}，本次答对 ${result.correct} / ${result.total} 题，得分 ${result.score} 分。`;
    byId("submitExam").textContent = "已提交";
    notice("模拟成绩已生成，可查看每题答案。");
    byId("examResult").hidden = false;
    window.scrollTo({top:0, behavior:"instant"});
  } catch (error) {
    notice(error.message || "网络异常，请重试");
    byId("submitExam").disabled = false;
  }
});
fetch("data", {cache:"no-store"}).then(async response => {
  if (!response.ok) throw new Error("模拟试卷已结束或无法读取，请重新生成。");
  render(await response.json());
}).catch(error => { byId("questions").replaceChildren(element("div", "card loading", error.message)); });
