const labels: Record<string, string> = {
  RECEIVED: "已接收",
  TRIAGING: "分诊中",
  INVESTIGATING: "调查中",
  DIAGNOSED: "已诊断",
  PLANNING: "生成方案",
  WAITING_APPROVAL: "等待审批",
  EXECUTING: "执行中",
  VERIFYING: "验证中",
  RESOLVED: "已恢复",
  NEED_HUMAN: "需要人工",
  FAILED: "处理失败",
  firing: "告警中",
  resolved: "已恢复",
  PROCESSING: "自动处理中",
  AWAITING_APPROVAL: "等待审批",
  ATTENTION: "需要关注",
  IDLE: "运行正常",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`status-badge status-${status.toLowerCase().replaceAll("_", "-")}`}>
      <i aria-hidden="true" />
      {labels[status] ?? status}
    </span>
  );
}
