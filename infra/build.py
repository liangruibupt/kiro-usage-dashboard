"""本地构建 Lambda 资产（无 Docker）。

- build_api_assets():
    1) 把看板 API 依赖（fastapi/mangum/pydantic/...）以 manylinux2014 x86_64 / cp312 wheel
       安装到 build/api_deps/python（Lambda layer 布局）；
    2) 把 backend/api/* + backend/shared/*（含 derivations, athena_client）平铺到 build/api_app，
       使 handler.py 的 `from main import app`、`from shared...` 相对回退可用。
  返回 (deps_layer_dir, app_code_dir)。

设计与 data_stack 的 ETL 打包一致：不依赖 Docker bundling。
依赖安装做了缓存标记（requirements 变化才重装）。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
BACKEND = os.path.abspath(os.path.join(HERE, "..", "backend"))

API_DEPS = [
    "fastapi==0.115.0",
    "mangum==0.17.0",
    "pydantic==2.9.2",
    "pydantic-core==2.23.4",
    "starlette==0.38.6",
    "anyio==4.6.0",
    "sniffio==1.3.1",
    "idna==3.10",
    "typing_extensions==4.12.2",
    "annotated-types==0.7.0",
]


def _sig(items: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(items)).encode()).hexdigest()[:16]


def build_api_assets() -> tuple[str, str]:
    deps_dir = os.path.join(BUILD, "api_deps")
    app_dir = os.path.join(BUILD, "api_app")
    _build_deps_layer(deps_dir)
    _assemble_app(app_dir)
    return deps_dir, app_dir


def _build_deps_layer(deps_dir: str) -> None:
    py_dir = os.path.join(deps_dir, "python")
    stamp = os.path.join(deps_dir, ".sig")
    sig = _sig(API_DEPS)
    if os.path.isfile(stamp) and open(stamp).read().strip() == sig and os.path.isdir(py_dir):
        return  # 已构建且依赖未变
    if os.path.isdir(deps_dir):
        shutil.rmtree(deps_dir)
    os.makedirs(py_dir, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--only-binary=:all:", "--platform", "manylinux2014_x86_64",
        "--python-version", "3.12", "--implementation", "cp",
        "--target", py_dir, *API_DEPS,
    ]
    subprocess.check_call(cmd)
    _trim(py_dir)
    with open(stamp, "w") as f:
        f.write(sig)


def _assemble_app(app_dir: str) -> None:
    if os.path.isdir(app_dir):
        shutil.rmtree(app_dir)
    os.makedirs(app_dir, exist_ok=True)
    # 平铺 API 代码（去掉包前缀，handler.py 用 `from main import app` 回退）
    for fn in ["handler.py", "main.py", "deps.py", "settings.py", "admin_users.py", "tier_requests.py", "model_multipliers.py", "mcp_server.py"]:
        shutil.copy2(os.path.join(BACKEND, "api", fn), app_dir)
    # shared 作为子包保留（athena_client 里 `from shared.athena_client` 回退）
    shared_dst = os.path.join(app_dir, "shared")
    os.makedirs(shared_dst, exist_ok=True)
    open(os.path.join(shared_dst, "__init__.py"), "w").close()
    for fn in ["athena_client.py", "derivations.py", "recommendations.py"]:
        shutil.copy2(os.path.join(BACKEND, "shared", fn), shared_dst)


def _trim(py_dir: str) -> None:
    """裁剪无关文件，压缩 layer 体积。"""
    for root, dirs, files in os.walk(py_dir):
        for d in list(dirs):
            if d in ("__pycache__", "tests", "test"):
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                dirs.remove(d)
        for f in files:
            if f.endswith((".pyc", ".pyo")):
                os.remove(os.path.join(root, f))


if __name__ == "__main__":
    d, a = build_api_assets()
    print("deps layer:", d)
    print("app code:", a)
