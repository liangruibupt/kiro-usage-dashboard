"""backend/agent/deploy.py 单测：Agent 执行角色策略的 ECR 权限按 --image 收敛。

backend/agent 无 __init__.py，按绝对路径用 importlib 加载为模块对象；只测纯函数，不触碰 AWS。
"""
import importlib.util
from pathlib import Path

import pytest

_DEPLOY_PATH = Path(__file__).resolve().parent.parent / "agent" / "deploy.py"


@pytest.fixture(scope="module")
def dp():
    spec = importlib.util.spec_from_file_location("agent_deploy_under_test", _DEPLOY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IMAGE = "123456789012.dkr.ecr.us-west-2.amazonaws.com/kiro-usage-agent:v1"


def _policy(dp, image=IMAGE):
    return dp.build_policy("123456789012", "us-west-2", "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                           "KiroDash-Data-wg", "curated-bkt", "results-bkt", image)


def test_build_policy_scopes_ecr_pull_to_image_repo(dp):
    st = {s["Sid"]: s for s in _policy(dp)["Statement"]}
    assert "ECR" not in st
    assert st["EcrAuth"]["Action"] == ["ecr:GetAuthorizationToken"]
    assert st["EcrAuth"]["Resource"] == "*"
    assert st["EcrPullAgentImage"]["Action"] == ["ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
    assert st["EcrPullAgentImage"]["Resource"] == (
        "arn:aws:ecr:us-west-2:123456789012:repository/kiro-usage-agent")
    # Glue 只读仍保留 "*"（有意，见 deploy.py 注释）
    assert st["Glue"]["Resource"] == "*"


@pytest.mark.parametrize("image,expected", [
    (IMAGE, ("123456789012", "us-west-2", "kiro-usage-agent")),
    ("210987654321.dkr.ecr.eu-central-1.amazonaws.com/team/kiro-agent@sha256:" + "a" * 64,
     ("210987654321", "eu-central-1", "team/kiro-agent")),
    ("123456789012.dkr.ecr.us-east-1.amazonaws.com/kiro-agent",
     ("123456789012", "us-east-1", "kiro-agent")),
])
def test_parse_ecr_image(dp, image, expected):
    assert dp.parse_ecr_image(image) == expected


@pytest.mark.parametrize("image", [
    "",
    "kiro-usage-agent:v1",
    "public.ecr.aws/docker/library/python:3.12-slim",
    "docker.io/library/python:3.12",
    "12345.dkr.ecr.us-west-2.amazonaws.com/kiro:v1",
])
def test_parse_ecr_image_rejects_non_ecr(dp, image):
    with pytest.raises(ValueError):
        dp.parse_ecr_image(image)
    with pytest.raises(ValueError):
        _policy(dp, image)
