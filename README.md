HidenCloud 自动续期 - 青龙面板适配版（完整版）

https://github.com/vistal8/Hidencloud_QingLong/

依赖：cloudscraper beautifulsoup4 requests

Hidencloud 青龙面板续期脚本

创建青龙变量HIDEN_COOKIE

优化cookies 仅需要remember_web开头的cookies 第一次默认读取HIDEN_COOKIE第二次默认读取存储在本地的config.json的cookies 建议每6-10小时运行一次

全部本地存储，建议文件夹内运行来保证路径清洁，避免配置文件被其他脚本覆盖

增加wxpusher推送变量：WXPUSHER_APP_TOKEN WXPUSHER_UID 建议每个ip跑一个账号，不要用代理.避免被多账号检测！检测到就需要刷脸！！！而且成功率不高！

青龙任务命令：task HidenCloud.py
