# -*- coding: utf-8 -*-
"""
api/core/hot_seed.py — 内置热点种子（唯一真源）

用途（两处，禁止各写一份）：
  ① `data/clean_chunk.py`：重建库时的初始数据（走 raw → signal → 物化 同一条链路）
  ② `hotspot_collector.seed_if_empty()`：三级兜底的最后一级（全源不可达且库为空时）

重要性：种子必须走与真实采集完全相同的管道，否则会出现"种子数据没有 signal_id /
first_seen_at、默认视图行为不一致"的隐蔽分裂。因此本文件只提供原始条目，
由 hot_signal.run_pipeline 负责聚合与物化。

domain 字段刻意不写：领域由 domain_keywords 判定，种子也走同一套判定，
这样种子数据同时是领域词表的回归样本。
"""

# 固定批次（非时间戳），且必须"可排序地最旧"——聚合按 MAX(batch_id) 取各源最新
# 快照，若批次号用 seed... 这类字符串，字典序会大于 2026... 的真实批次，
# 导致真实采集被种子挡住（已踩过）。故用纪元时间。
SEED_BATCH = "19700101T000000"


def _item(source_code: str, title: str, summary: str, rank: int,
          raw_heat: int | None = None, url: str = "") -> dict:
    return {
        "source_code": source_code,
        "title": title,
        "summary": summary,
        "url": url,
        "rank": rank,
        "raw_heat": raw_heat,
    }


SEED_ITEMS: list[dict] = [
    _item("weibo", "端侧大模型推理加速，终端 AI 迎来拐点",
          "端侧推理让手机本地跑大模型成为可能，隐私与延迟同时受益，硬件厂商集体跟进。",
          1, 98210, "https://s.weibo.com/weibo?q=%E7%AB%AF%E4%BE%A7%E5%A4%A7%E6%A8%A1%E5%9E%8B"),
    _item("zhihu", "开源向量数据库 RAG 检索增强成标配",
          "RAG 检索增强从可选优化变成工程标配，向量数据库选型与召回质量成为讨论重点。",
          2, 8600, "https://www.zhihu.com/hot"),
    _item("weibo", "一人食极简备餐法走红",
          "一人食场景下，周末集中备餐 + 分装的极简方案在小红书与微博同时走热。",
          2, 76300, "https://s.weibo.com/weibo?q=%E4%B8%80%E4%BA%BA%E9%A3%9F"),
    _item("bilibili", "通勤碎片化时间管理实操",
          "通勤 40 分钟能做什么？碎片化时间管理的实操向视频播放量走高。",
          4, 412000, "https://www.bilibili.com/v/popular/all"),
    _item("baidu", "美联储加息预期扰动权益资产",
          "加息预期反复，权益资产波动加大，仓位管理与资产配置类内容搜索量上升。",
          1, 1289000, "https://top.baidu.com/board"),
    _item("zhihu", "复利效应下的现金仓位再平衡",
          "复利效应被重新讨论：现金仓位在高利率环境下如何再平衡，成为理财话题焦点。",
          5, 5200, "https://www.zhihu.com/hot"),
    _item("weibo", "早C晚A护肤翻车，敏感肌避雷清单",
          "成分党热度下，新手盲目叠加酸类与维 A 导致屏障受损的案例增多，避雷清单类内容高收藏。",
          3, 65400, "https://s.weibo.com/weibo?q=%E6%97%A9C%E6%99%9AA"),
    _item("douyin", "国货彩妆性价比测评热度走高",
          "平价彩妆测评与平替对比成为热门选题，真实上脸内容更易获得收藏。",
          2, 331000, "https://www.douyin.com/hot"),
    _item("baidu", "AI 编程课真假难辨，成人自学路径图走红",
          "成人再就业需求下，零基础自学编程的路径规划与资源筛选内容搜索走高。",
          2, 486000, "https://top.baidu.com/board"),
    _item("toutiao", "少儿编程到底该不该学引热议",
          "家长群体对少儿编程的价值与焦虑并存，逻辑思维培养类科普内容稳定输出。",
          1, 275000, "https://www.toutiao.com/hot-event/hot-board/"),
]
