# Hidencloud_QingLong
Hidencloud 青龙面板续期脚本
基于https://github.com/c935289832/hidencloud_renew 作者的脚本进行修改
全部本地存储，建议文件夹内运行来保证路径清洁，避免配置文件被其他脚本覆盖
需要安装python依赖 cloudscraper  beautifulsoup4
至于cookies如何获取 https://dash.hidencloud.com/ 登陆这个网址 管理你的主机 然后通过cookie editor 导出cookies 测试只需要TOKEN=后面的即可 前面的不复制也可以登陆续期
需要手动创建config.json文件 格式如下 
{
  "HIDEN_COOKIE": "",
  "WP_APP_TOKEN_ONE": "",
  "WP_UIDs": "",
}
