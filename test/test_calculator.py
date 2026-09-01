from tools.calculator import calculate
def test_add():
    assert calculate("123 + 456") == 579


def test_sub():
    assert calculate("100 - 30") == 70


def test_multiply():
    assert calculate("10 * 20") == 200


def test_divide():
    assert calculate("100 / 4") == 25


def test_parentheses():
    assert calculate("(10 + 20) * 3") == 90


def test_negative():
    assert calculate("-10 + 20") == 10


def test_power():
    assert calculate("10 ** 2") == 100


def test_mod():
    assert calculate("13 % 10") == 3


def test_floor_div():
    assert calculate("13 // 10") == 1


def test_zero_division():
    assert calculate("10 / 0") == "分母不能为零"


def test_invalid_expression():
    assert calculate("123 + abc") == "表达式中存在不支持的节点"