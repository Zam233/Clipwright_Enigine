"""验证 IsoBase timeout 参数是否生效。"""
import os, sys, asyncio, time
os.chdir(r"D:\Clipweight")
sys.path.insert(0, r"D:\Clipweight")
from clipwright.services.llm import LLMService


async def main():
    llm = LLMService()
    t0 = time.time()
    try:
        await asyncio.wait_for(
            llm.generate(
                messages=[
                    {"role": "system", "content": "用 500 字描述量子力学（慢速长输出）"},
                    {"role": "user", "content": "开始"},
                ],
                timeout=5,
            ),
            timeout=60,
        )
        print("timeout=5 请求成功返回（未触发 5s 超时）")
    except asyncio.TimeoutError:
        print(f"外部 wait_for 60s 超时 —— IsoBase timeout 参数未生效！")
    except Exception as e:
        print(f"耗时 {round(time.time()-t0, 1)}s 抛异常: {str(e)[:120]}")


asyncio.run(main())
