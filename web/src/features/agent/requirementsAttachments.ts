import type { CreativeBrief, ProductionPlan } from '@/types/persona';

export interface MessageAttachments {
  creative_brief: CreativeBrief | null;
  production_plan: ProductionPlan | null;
}

/**
 * resolveMessageAttachments — 根据当前会话状态决定 assistant 消息应挂载哪些附件。
 *
 * 规则：
 * - `creative_brief` 仅在"有简报且尚无规划书"时附带（规划书已生成的消息不再挂旧简报，避免消息附件错配）；
 * - `production_plan` 只要存在即附带。
 */
export function resolveMessageAttachments(
  status: string | undefined,
  brief: CreativeBrief | null | undefined,
  plan: ProductionPlan | null | undefined,
): MessageAttachments {
  void status; // 保留参数签名以对齐调用方，供后续按状态精细化时使用
  return {
    creative_brief: brief && !plan ? brief : null,
    production_plan: plan ?? null,
  };
}
