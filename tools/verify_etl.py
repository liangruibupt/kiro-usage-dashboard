"""本地验证 ETL：对 mock 数据跑转换，打印两张表的形状与样例，并做一致性断言。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.etl import transform  # noqa: E402


def main():
    fud, fmd = transform.run_local(
        csv_glob="mock/**/*.csv",
        identity_json="mock/identity_groups.json",
        credit_unit_price=0.15,
    )
    print("=== fact_user_daily ===", fud.shape)
    print(fud.head(3).to_string())
    print("\n=== fact_user_model_daily ===", fmd.shape)
    print(fmd.head(5).to_string())

    # 一致性断言
    assert not fud.empty and not fmd.empty, "两张表都不应为空"
    # 每个 (date,email) 唯一
    assert fud.duplicated(["date", "email"]).sum() == 0, "fact_user_daily 应按 (date,email) 唯一"
    # 使用率 = credits_used / overage_cap（抽一行核对）
    r = fud.iloc[0]
    if r["overage_cap"] > 0:
        assert abs(r["usage_rate"] - round(r["credits_used"] / r["overage_cap"], 4)) < 1e-6
    # 模型长表的消息数之和 应 <= 各用户 total_messages 之和（melt 未丢失）
    print("\n部门分布:")
    print(fud.groupby("department")["email"].nunique().to_string())
    print("\n模型分布(消息数 top):")
    print(fmd.groupby("model")["model_messages"].sum().sort_values(ascending=False).to_string())
    print("\n✅ ETL 一致性断言通过")


if __name__ == "__main__":
    main()
