# T-09 点云文件登记与上传

| 项 | 内容 |
| --- | --- |
| 阶段 | P3 |
| 依赖前置 | T-06 |
| 产出 | API-19 预签名分片、pointcloud_file 登记、配准残差校验、file_object_link、状态机 READY |
| 验收门 | 直传不经应用服务；残差超限被拒；分片校验可重放 |
| 预估工作量 | 5 人日 / 4 卡 |
| 人工主导 | 否 |
| 外部阻塞 | 阻塞于 C-01（点云体量）、C-05（SRID 与带号） |

## 1. 目标

让 5–10 GB 的单座点云能稳定上传并带着配准质量信息入库。

## 2. 输入

- API-19、API-08
- pointcloud_file / file_object_link / control_point
- V1.2 §5.4.5 上传流程

## 3. 改动范围

- 允许：backend/app/modules/media/pointcloud/**、frontend/src/views/upload/**
- 禁止：在应用服务里读写文件流；禁止改 pointcloud_file 字段

超出上述范围即视为越权改动，即使代码正确也退回。

## 4. 实现要点

- 分片大小 16 MB，预签名 URL 有效期 2 小时，超时重签
- complete 时校验 ETag 与整体哈希，任一不符返回 422 + BT-80002
- 登记必须带 `registration_rms ≤ 10 mm`、`control_residual ≤ 10 mm`，超限 422 + BT-40003
- 点密度 < 100 点/㎡ 拒绝入库并提示返工（阈值待实测）

## 5. DoD（可验证的完成定义）

- 10 GB 样例上传内存峰值 < 512 MB
- 残差 12 mm 的登记被拒且错误码正确
- 断点续传可从中断分片继续
- 入库后 status=READY 且 file_object_link 建联

## 6. 验收用例

| # | 输入 | 操作 | 期望结果 |
| --- | --- | --- | --- |
| 1 | 上传中断后重传同一 uploadId | PUT 剩余分片 | 已传分片跳过，最终 complete 成功 |
| 2 | registration_rms=12 mm | POST 登记 | 422，code=BT-40003 |
| 3 | 上传 .exe 伪装为 .laz | POST /files/init | 422，code=BT-80001 |

## 7. 依赖与顺序

T-06 之后；T-14（转换）依赖它。

## 8. 常见坑

- 不要用 request.body 读大文件
- 不要把分片 URL 存数据库
- 不要信任客户端给的点密度
