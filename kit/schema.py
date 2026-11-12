"""最小 JSON Schema 校验（只支持 type / required / enum / properties / items），无外部依赖。"""
import json
import sys
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"

TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool, "null": type(None)}


def check(value, schema, path="$"):
    errs = []
    t = schema.get("type")
    if t:
        ts = t if isinstance(t, list) else [t]
        if not any(isinstance(value, TYPES[x]) and not (x == "integer" and isinstance(value, bool)) for x in ts):
            return [f"{path}: 类型应为 {ts}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: 取值应为 {schema['enum']}")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: 缺少 {k}")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errs += check(value[k], sub, f"{path}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errs += check(v, schema["items"], f"{path}[{i}]")
    return errs


def validate_file(data_path, schema_name):
    schema = json.loads((SCHEMA_DIR / f"{schema_name}.schema.json").read_text(encoding="utf-8"))
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    return check(data, schema)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("用法：schema.py <数据文件.json> <schema 名：product|project>")
    errs = validate_file(sys.argv[1], sys.argv[2])
    if errs:
        print("校验失败：")
        for e in errs:
            print("  -", e)
        sys.exit(1)
    print("校验通过")
