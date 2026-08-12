"""blindfold_devtools must never ship in the wheel (ADR-0047 §2). pyproject.toml's wheel
target keeps `packages = ["src/blindfold"]` unchanged -- this proves that holds by actually
building the wheel and inspecting its contents, rather than trusting the config by eye.
"""

import pathlib
import subprocess
import zipfile


def _build_wheel(out_dir: pathlib.Path) -> pathlib.Path:
    repo_root = pathlib.Path(__file__).parent.parent
    subprocess.run(
        ["uv", "build", "--wheel", "-o", str(out_dir)],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"
    return wheels[0]


def test_wheel_contains_no_blindfold_devtools_files(tmp_path):
    wheel_path = _build_wheel(tmp_path)

    with zipfile.ZipFile(wheel_path) as wheel:
        names = wheel.namelist()

    devtools_files = [name for name in names if "blindfold_devtools" in name]
    assert not devtools_files, (
        f"blindfold_devtools must never ship in the wheel, but found: {devtools_files}"
    )
    assert any(name.startswith("blindfold/") for name in names), (
        "sanity check: the wheel should still contain the blindfold package itself"
    )
