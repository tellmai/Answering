import pytest
from webassistant.models import Question


@pytest.fixture
def sample_bank():
    questions = []
    for n in range(1, 181):
        kind = ("单选", "多选", "判断")[(n - 1) % 3]
        options = {"A": "正确", "B": "错误"} if kind == "判断" else {"A": f"演示选项甲{n}", "B": f"演示选项乙{n}", "C": f"演示选项丙{n}"}
        answers = ("A", "C") if kind == "多选" else ("B",)
        questions.append(Question(n, kind, f"第 {n} 道合成练习题，属于{kind}题。", options, answers, (1,)))
    return questions
