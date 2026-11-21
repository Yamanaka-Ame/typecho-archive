# `.dat` 备份文件格式

本文档描述的格式由 Typecho 的备份组件生成，实现位于源码包内：

- `var/Widget/Backup.php` —— 表与字段白名单、导出/导入流程
- `var/Typecho/Common.php` 的 `buildBackupBuffer()` / `extractBackupBuffer()` —— 字节级编码

---

## 一、整体布局

```
┌──────────────────────────────┐
│ 魔数  %TYPECHO_BACKUP_0001%  │  21 字节
├──────────────────────────────┤
│ 记录 1                       │  变长
│ 记录 2                       │
│ …                            │
├──────────────────────────────┤
│ 魔数  %TYPECHO_BACKUP_0001%  │  21 字节
└──────────────────────────────┘
```

魔数模板是 `%TYPECHO_BACKUP_XXXX%`（长 21），`XXXX` 被替换成版本号 `0001`。
文件头与文件尾各一份，用途是让导入端确认「这是一份完整备份」——尾部缺失通常意味着
导出过程被中断。

**记录之间没有任何分隔符**，也没有全局的目录表。要定位第 N 条记录，只能从头部顺序往下读。

---

## 二、单条记录

```
偏移   长度   字段
 0      2     type        uint16 小端   记录类型，见下表
 2      2     headerLen   uint16 小端   JSON 字段表的字节数
 4      4     bodyLen     uint32 小端   正文区字节数
 8    headerLen  header   UTF-8 JSON    {"字段名": 字节长度 | null, …}
 …    bodyLen   body      原始字节      按 header 的顺序首尾相接
 …      32     md5        ASCII        前三个区段 MD5 的小写十六进制
```

对应 PHP 的 `pack('vvV', $type, strlen($header), strlen($body))`，
在 Python 中等价于 `struct.pack('<HHI', …)`（注意 Python 没有 `V` 这个格式符）。

### header 的语义

header 是一个**有序**的 JSON 对象，键是字段名，值是**该字段在 body 中占用的字节数**：

```json
{"cid":2,"title":25,"slug":16,"created":10,"modified":10,"text":18941,
 "order":2,"authorId":1,"template":null,"type":4,"status":6,"password":null,
 "commentsNum":1,"allowComment":1,"allowPing":1,"allowFeed":1,"parent":1}
```

- 数字 = 字节长度，**不是**字段值；
- `null` = 该字段是 SQL NULL，在 body 中**不占任何字节**；
- 键的顺序就是 body 中字段的拼接顺序；
- `sum(非 null 长度) == bodyLen`，这是最直接的完整性判据。

因为所有值都以原始字节存放，数字字段（`cid`、`created` 等）在 body 里是十进制字符串，
比如 `cid=27` 占 2 字节，就是字符 `"27"`。

### md5 的范围

```php
md5($meta . $header . $body)          // $meta 是那 8 个原始字节
```

即 **包含** 8 字节记录头、**不包含** 末尾 32 字节的校验值本身。校验值以小写十六进制
文本存放，恰好 32 字节。

---

## 三、记录类型

| type | 表 | 说明 |
|---:|---|---|
| 1 | `contents` | 文章、独立页面、附件 |
| 2 | `comments` | 评论 |
| 3 | `metas` | 分类与标签 |
| 4 | `relationships` | 内容与分类/标签的关联 |
| 5 | `users` | 用户 |
| 6 | `fields` | 自定义字段（主题附加数据） |

导出时按上表顺序逐表分页写入（每页 20 行），所以文件里同类型的记录是连续的。
插件可以通过 `pluginHandle()->call('export', $fp)` 追加自己的记录，其 `type` 编号
由插件定义，解析端应原样保留而不是丢弃。

### 各表的字段白名单

只有下面这些字段会被写入，数据库里多出来的列（例如部分站点扩展出来的 `views`、`likes`、`qqk`）会被丢弃：

```
contents       cid, title, slug, created, modified, text, order, authorId,
               template, type, status, password, commentsNum,
               allowComment, allowPing, allowFeed, parent
comments       coid, cid, created, author, authorId, ownerId, mail, url,
               ip, agent, text, type, status, parent
metas          mid, name, slug, type, description, count, order, parent
relationships  cid, mid
users          uid, name, password, mail, url, screenName,
               created, activated, logged, group, authCode
fields         cid, name, type, str_value, int_value, float_value
```

---

## 四、手工拆一条记录

以一份实际备份的第一条记录为例（文件偏移 21）：

```
00000015  01 00 d5 00 90 00 00 00                ← 记录头
0000001d  7b 22 63 69 64 22 3a 31 2c 22 74 69 …   ← header 213 字节
…                                                ← body 144 字节
…                                                ← md5 32 字节
```

- `01 00` → type = 1（contents）
- `d5 00` → headerLen = 213
- `90 00 00 00` → bodyLen = 144

header 解析后是 `{"cid":1,"title":20,"slug":5,"created":10,"modified":10,"text":80,…}`，
各长度相加正好 144。body 从头切分得到：

| 字段 | 长度 | 内容 |
|---|---:|---|
| cid | 1 | `1` |
| title | 20 | `欢迎使用 Typecho`（UTF-8，中文 3 字节/字） |
| slug | 5 | `start` |
| created | 10 | `1690710360` |
| modified | 10 | `1693404466` |
| text | 80 | `<!--markdown-->如果您看到这篇文章,表示您的 blog 已经安装成功.` |
| template | null | 无字节 |
| … | | |

---

## 五、最小可用的 Python 解析器

```python
import hashlib, json, struct
from pathlib import Path

MAGIC_LEN = len("%TYPECHO_BACKUP_XXXX%")   # 21

def parse(path):
    data = Path(path).read_bytes()
    pos, records = MAGIC_LEN, []
    while pos + 8 <= len(data):
        if len(data) - pos == MAGIC_LEN:        # 文件尾魔数
            break
        meta = data[pos:pos + 8]
        typ, hlen, blen = struct.unpack("<HHI", meta)
        pos += 8
        header = data[pos:pos + hlen]; pos += hlen
        body   = data[pos:pos + blen]; pos += blen
        stored = data[pos:pos + 32];   pos += 32
        assert stored == hashlib.md5(meta + header + body).hexdigest().encode()

        schema = json.loads(header)
        row, cursor = {}, 0
        for name, length in schema.items():
            if length is None:
                row[name] = None
            else:
                row[name] = body[cursor:cursor + length].decode("utf-8", "replace")
                cursor += length
        records.append((typ, row))
    return records
```

写入端要保证与 PHP 输出一致：JSON 用**紧凑**形式（`separators=(',', ':')`，
没有空格，`ensure_ascii` 对纯 ASCII 键名无影响），长度按 **UTF-8 字节数** 计算而不是字符数。

---

## 六、版本 `FILE`

早期实现用过另一套记录头：`pack('vvv', type, headerLen, 0)`，共 6 字节，
`bodyLen` 不在头里，而由 header 中各长度求和得出。解析时按魔数第 17–20 字节区分：

```
len == 6  →  version == "FILE"
len == 8  →  version == "0001"
```

本工具两者都支持，未知版本会打印提示并按 `0001` 规则尝试。

---

## 七、导入端的约束（决定了备份里能有什么）

`Widget\Backup::extractData()` 在导入时对每张表执行一次 `TRUNCATE` 再逐行 `INSERT`，
并且只写白名单字段。由此可以推出几件事：

- 备份**不包含** `options` 表——站点标题、主题、路由都不在里面，恢复后用的是目标站的设置；
- 数据库里新增的列在恢复后会取默认值（例如部分站点扩展的浏览量、点赞数会归零）；
- 由于逐行插入，外键与自增序列需要在导入完成后自行处理（MySQL 会按最大值续上，PG 需要显式 `ALTER SEQUENCE`）。
