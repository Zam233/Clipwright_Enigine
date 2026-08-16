import { describe, it, expect } from 'vitest';
import { resolveMessageAttachments } from './requirementsAttachments';
import type { CreativeBrief, ProductionPlan } from '@/types/persona';

const brief: CreativeBrief = {
  title: '创意简报',
  overview: '概述',
  target_audience: '受众',
  core_message: '核心信息',
  style_direction: '风格',
  structure_suggestion: '结构',
  duration_estimate: '时长',
  key_elements: [],
  special_requirements: [],
};
const plan: ProductionPlan = { markdown_content: '# 规划书' };

describe('resolveMessageAttachments', () => {
  it('brief only -> attaches creative_brief, no production_plan', () => {
    const att = resolveMessageAttachments('brief_ready', brief, null);
    expect(att.creative_brief).toBe(brief);
    expect(att.production_plan).toBeNull();
  });

  it('plan only -> attaches production_plan, no creative_brief', () => {
    const att = resolveMessageAttachments('plan_ready', null, plan);
    expect(att.creative_brief).toBeNull();
    expect(att.production_plan).toBe(plan);
  });

  it('brief + plan -> only attaches plan (drops stale brief)', () => {
    const att = resolveMessageAttachments('plan_ready', brief, plan);
    expect(att.creative_brief).toBeNull();
    expect(att.production_plan).toBe(plan);
  });

  it('both empty -> both null', () => {
    const att = resolveMessageAttachments('gathering', null, null);
    expect(att.creative_brief).toBeNull();
    expect(att.production_plan).toBeNull();
  });
});
