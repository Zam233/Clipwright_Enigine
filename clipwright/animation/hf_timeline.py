"""HyperFrames 合成用的「真实时间轴」运行时片段（零第三方依赖）。

背景（审计 HFR-1 / HFR-2）
--------------------------
旧实现把 overlay 的可见性交给自写 `setTimeout` 墙钟 JS，并且**没有**向
`window.__timelines` 注册时间轴。两处都与上游契约冲突：

1. 非 seek-safe：HyperFrames 逐帧 seek + 多 worker 并行抓帧，任何"依赖先经过某帧
   才成立"的状态（timer / 累加状态 / 事件驱动动画）都会错帧。
   契约原文见上游 `skills/hyperframes-core/references/determinism-rules.md`：
   "The renderer takes a time value and produces a pixel buffer... If you find
   yourself reaching for `setTimeout`, `requestAnimationFrame`, or
   `addEventListener` to drive a visual, rebuild it as a tween on the timeline
   instead."
2. 缺注册触发 producer 的 timeline 就绪轮询超时（上游 lint：
   `missing_data_no_timeline` —— "adding 45 s to every render"）。本机实测：同一份
   180 帧 overlay 合成 108.2s → 注册后 18.0s；135 帧 MG 产物 129.1s → 22.9s。

实现契约
--------
上游 `packages/core/src/runtime/init.ts` 明确 timeline 解析是**宽松**的：
"A `window.__timelines` entry is authored content and may be a PARTIAL
RuntimeTimelineLike — e.g. duration/seek only, no `pause()`. Such compositions
render fine (the render path only seeks and never pauses)"；GSAP 适配器
（`packages/core/src/adapters/gsap.ts`）只要求：
`{ duration(): number, totalDuration?(): number, seek(t, suppressEvents?): unknown, pause?(): unknown }`。

因此这里注册的是我们**自己的** RuntimeTimelineLike 实现：把既有 `hf-*` / `mg_*`
CSS `@keyframes` 动画暂停（WAAPI `Animation.pause()`），并在 `seek(t)` 时把每个元素的
动画 `currentTime` 设为「合成时间 - 元素 data-start」——每一帧都是 t 的纯函数，
不依赖任何墙钟状态。

为什么不引入 GSAP：上游脚手架用 CDN 加载 gsap（`packages/cli/src/templates/blank/
index.html`），而确定性渲染规则禁止渲染期网络抓取；本地内联/打包 GSAP 又会引入
非 OSI 许可的第三方运行时。用 CSS 关键帧 + WAAPI seek 可以在**零新增运行时依赖、
零许可变更**的前提下拿到同样的确定性，并完整保留既有动画视觉。
"""

from __future__ import annotations

# 运行时片段：注册 RuntimeTimelineLike 到 window.__timelines[<data-composition-id>]。
TIMELINE_JS = """(function(){
window.__timelines=window.__timelines||{};
var root=document.getElementById('root')||document.querySelector('[data-composition-id]');
var compId=(root&&root.getAttribute('data-composition-id'))||'main';
var scopes=Array.prototype.slice.call(document.querySelectorAll('.hf-el, .mg-el'));
var total=root?(parseFloat(root.getAttribute('data-duration'))||0):0;
if(!(total>0)){
  total=scopes.reduce(function(max,el){
    var s=parseFloat(el.getAttribute('data-start'))||0;
    var d=parseFloat(el.getAttribute('data-duration'))||0;
    return Math.max(max,s+d);
  },0);
}
function collect(el){
  var out=[];
  if(typeof el.getAnimations!=='function'){return out;}
  try{out=el.getAnimations({subtree:el.getAttribute('data-anim-subtree')==='1'});}
  catch(e){try{out=el.getAnimations();}catch(e2){out=[];}}
  for(var i=0;i<out.length;i++){try{out[i].pause();}catch(e3){}}
  return out;
}
var specs=scopes.map(function(el){
  return {el:el,start:parseFloat(el.getAttribute('data-start'))||0,anims:collect(el)};
});
function settle(el,localMs){
  // WAAPI 不可用时的确定性回退：按 data-anim-dur 线性淡入
  var ad=(parseFloat(el.getAttribute('data-anim-dur'))||0.4)*1000;
  var p=ad>0?Math.min(1,Math.max(0,localMs/ad)):1;
  el.style.opacity=String(p);
}
function seek(t){
  var time=(typeof t==='number'&&isFinite(t))?t:0;
  for(var i=0;i<specs.length;i++){
    var s=specs[i];
    var localMs=Math.max(0,(time-s.start)*1000);
    if(!s.anims.length){s.anims=collect(s.el);}
    if(s.anims.length){
      for(var j=0;j<s.anims.length;j++){
        try{s.anims[j].currentTime=localMs;}catch(e){}
      }
    }else{
      settle(s.el,localMs);
    }
  }
}
window.__timelines[compId]={
  duration:function(){return total;},
  totalDuration:function(){return total;},
  pause:function(){
    for(var i=0;i<specs.length;i++){
      for(var j=0;j<specs[i].anims.length;j++){
        try{specs[i].anims[j].pause();}catch(e){}
      }
    }
  },
  seek:seek
};
try{seek(0);}catch(e){}
})();"""


def timeline_script() -> str:
    """返回可直接嵌入 `<script>` 的运行时片段。"""
    return TIMELINE_JS
