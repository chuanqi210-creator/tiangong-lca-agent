# D-18 无前端后端本地部署

这个容器只提供服务控制面；科研事实仍由 Project Record 管理。镜像固定 Python 基础
镜像摘要和带哈希的 Python 依赖，以 UID/GID `10001` 非 root 运行，根文件系统只读。

1. 在仓库外创建仅当前用户可读的随机 Bearer token 文件（权限 `0600`）。
2. 复制 `service.env.example` 到仓库外，填写清小搭 OSS 的精确主机白名单和 token
   文件的绝对路径。不要把 token 写进环境样例、Git、日志或命令行参数。
3. 从仓库根目录运行：

   ```bash
   docker compose --env-file /安全路径/service.env \
     -f deploy/compose.yaml up --build
   ```

4. 机械检查 `GET http://127.0.0.1:8080/health/ready`。业务接口要求
   `Authorization: Bearer ...`；健康端点不回显凭证。

停止与恢复：

```bash
docker compose --env-file /安全路径/service.env -f deploy/compose.yaml down
docker compose --env-file /安全路径/service.env -f deploy/compose.yaml up
```

SQLite WAL、Project Record 和 Run Trace 位于 `d18-data` 卷。Worker 只保存操作元数据和
内容寻址引用；恢复过期租约不会重复已经接纳的科研事实或副作用。本阶段不包含清小搭
生产发布、域名、前端或云端凭证配置。
