# -*- coding: utf-8 -*-
"""演示版入口:强制演示模式(离线数据),其余与正式版一致"""
import os
os.environ["CF_DEMO"] = "1"
import fetcher_app
fetcher_app.main()
