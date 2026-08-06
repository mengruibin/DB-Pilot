"""
软删除标识列检测引擎单元测试（soft-delete-plan）。

覆盖检测规则：
  1. flag 型列名命中（is_deleted / del_flag / is_del / delete_flag / deleted / isdelete）
  2. deleted_at 型列名命中（deleted_at / delete_at / delete_time / deleted_time）
  3. 列注释命中（是否删除 / 删除标记 / 软删除）
  4. 反例不命中（create_time / update_time / status / deleted_by / 注释"删除人"）
  5. 类型合理性：flag 名 + 时间类型 → 归为 deleted_at 型
  6. 无软删除列 → 空列表
  7. 字段完整性：kind / deleted_value / active_value 正确
"""

from __future__ import annotations

from app.engine.soft_delete import detect_soft_delete_columns


def _col(
    name: str,
    type_: str = "int",
    comment: str = "",
    nullable: bool = True,
    is_primary: bool = False,
) -> dict:
    """构造适配器 get_columns 返回的单列字典。"""
    return {
        "name": name,
        "type": type_,
        "nullable": nullable,
        "is_primary": is_primary,
        "comment": comment,
    }


class TestSoftDeleteColumnName:
    """flag 型列名关键词命中。"""

    def test_flag_column_names(self):
        """常见 flag 型软删除列名应全部命中。"""
        for name in ("is_deleted", "del_flag", "is_del", "delete_flag", "deleted", "isdelete"):
            results = detect_soft_delete_columns([_col(name)])
            assert len(results) == 1, f"列名 {name} 应被识别为软删除标识"
            assert results[0].column == name
            assert results[0].kind == "flag"
            assert results[0].deleted_value == 1
            assert results[0].active_value == 0

    def test_deleted_at_column_names(self):
        """时间戳型软删除列名应全部命中，且归为 deleted_at 型。"""
        for name in ("deleted_at", "delete_at", "delete_time", "deleted_time"):
            results = detect_soft_delete_columns([_col(name, type_="datetime")])
            assert len(results) == 1, f"列名 {name} 应被识别为软删除标识"
            assert results[0].column == name
            assert results[0].kind == "deleted_at"
            assert results[0].deleted_value is None
            assert results[0].active_value is None

    def test_flag_name_with_temporal_type_downgraded(self):
        """flag 命名的列但类型是时间类 → 归为 deleted_at 型（类型合理性）。"""
        results = detect_soft_delete_columns([_col("is_deleted", type_="datetime")])
        assert len(results) == 1
        assert results[0].kind == "deleted_at"


class TestSoftDeleteColumnComment:
    """列注释关键词兜底命中（列名无关）。"""

    def test_flag_comment_keywords(self):
        """注释含「是否删除 / 删除标记 / 软删除」等应命中为 flag 型。"""
        for comment in ("是否删除", "删除标记", "软删除", "逻辑删除标识", "是否删除（0否1是）"):
            results = detect_soft_delete_columns([_col("delete_mark", comment=comment)])
            assert len(results) == 1, f"注释 {comment} 应命中软删除标识"
            assert results[0].kind == "flag"

    def test_deleted_at_comment_keywords(self):
        """注释含「删除时间」应命中为 deleted_at 型（列名非关键词时兜底注释）。"""
        results = detect_soft_delete_columns(
            [_col("delete_marker", type_="datetime", comment="删除时间")]
        )
        assert len(results) == 1
        assert results[0].kind == "deleted_at"
        assert "注释" in results[0].reason


class TestSoftDeleteNegative:
    """不应命中的反例。"""

    def test_common_audit_columns_not_detected(self):
        """create_time / update_time / status 等常规列不应被识别为软删除标识。"""
        columns = [
            _col("id", type_="bigint", is_primary=True),
            _col("create_time", type_="datetime"),
            _col("update_time", type_="datetime"),
            _col("status", type_="tinyint", comment="状态"),
            _col("created_by", type_="varchar(64)"),
        ]
        assert detect_soft_delete_columns(columns) == []

    def test_deleted_by_not_detected(self):
        """deleted_by（操作人元数据）及其注释「删除人」不应命中。"""
        results = detect_soft_delete_columns(
            [_col("deleted_by", type_="varchar(64)", comment="删除人")]
        )
        assert results == []

    def test_prefix_column_not_detected(self):
        """相似前缀但不等于关键词的列名不应命中（如 delete_log 不是 delete）。"""
        assert detect_soft_delete_columns([_col("delete_log", comment="删除日志")]) == []
        assert detect_soft_delete_columns([_col("update_time")]) == []

    def test_empty_columns(self):
        """空列列表返回空结果。"""
        assert detect_soft_delete_columns([]) == []


class TestSystemPromptSoftDeleteRule:
    """系统提示词软删除规则注入（soft-delete-plan）。"""

    def test_system_prompt_contains_soft_delete_rule(self):
        """build_agent_system_prompt 应包含软删除优先规则，且 format 正常。"""
        from app.prompts.agent import build_agent_system_prompt

        prompt = build_agent_system_prompt("mysql", "testdb", "127.0.0.1", "3306")
        assert "软删除优先" in prompt
        assert "describe_table" in prompt
        assert "UPDATE" in prompt
        # 连续拦截警告注入仍正常
        prompt_blocked = build_agent_system_prompt(
            "mysql", "testdb", "127.0.0.1", "3306", consecutive_blocks=3
        )
        assert "重要警告" in prompt_blocked


class TestSoftDeleteMixed:
    """混合场景与字段完整性。"""

    def test_picks_soft_delete_among_regular_columns(self):
        """含软删除列的表只命中标识列，且 reason 标注命中来源。"""
        columns = [
            _col("id", type_="bigint", is_primary=True),
            _col("name", type_="varchar(64)"),
            _col("is_deleted", type_="tinyint", comment="是否删除"),
            _col("create_time", type_="datetime"),
        ]
        results = detect_soft_delete_columns(columns)
        assert len(results) == 1
        assert results[0].column == "is_deleted"
        assert results[0].kind == "flag"
        assert "列名" in results[0].reason  # 列名命中优先于注释

    def test_reason_from_comment_when_name_unknown(self):
        """列名未命中、注释命中时，reason 标注来源为注释。"""
        results = detect_soft_delete_columns(
            [_col("mark_flag", type_="tinyint", comment="删除标记")]
        )
        assert len(results) == 1
        assert results[0].kind == "flag"
        assert "注释" in results[0].reason

    def test_multiple_soft_delete_columns_all_returned(self):
        """同时存在 is_deleted 与 deleted_at 时两者都返回，保持原顺序。"""
        columns = [
            _col("is_deleted", type_="tinyint"),
            _col("deleted_at", type_="datetime"),
        ]
        results = detect_soft_delete_columns(columns)
        assert [r.column for r in results] == ["is_deleted", "deleted_at"]
        assert [r.kind for r in results] == ["flag", "deleted_at"]
