# -*- coding: utf-8 -*-
"""领域内核：契约、端口与错误语义。

依赖方向（单向，不许反向）：
    api/routers  ->  core（端口/契约）  <-  adapters（sqlite|pg、cb|openai、mcp|skill）
    runtime/     ->  基础设施，不依赖 core 端口的具体实现

core 只放抽象与数据形状，不放框架代码、不放 SQL、不 import 具体驱动。
"""
from __future__ import annotations

__all__ = ["errors", "storage", "agent"]
