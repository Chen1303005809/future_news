"""当前项目内的 Kronos 预测入口。

模型实现由 ``kronos-model-arch`` 提供，模型权重由 Hugging Face Hub 加载；
本包只负责项目需要的 K 线预处理、预测编排和产物写出。
"""
