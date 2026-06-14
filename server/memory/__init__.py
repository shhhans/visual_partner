"""持久化记忆模块（slice 1：落库 + 跨会话留存，尚未回灌、未分链路）。

架构方向见项目记忆「持久化记忆架构方向」。本 slice 只做写入侧：
  回合结束 → 无条件 enqueue 到单写者后台 worker → 存原始 episode + LLM 抽取事实。
检索（prefetch/recall）已实现接口与底层查询，但暂未接入回复链路。

对外只暴露 worker 单例入口，Session 通过它 enqueue。
"""

from memory.worker import get_worker

__all__ = ["get_worker"]
