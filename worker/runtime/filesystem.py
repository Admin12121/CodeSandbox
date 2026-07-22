from __future__ import annotations

import base64
import posixpath

from .artifacts import directory_tar_bytes, extract_single_file, tar_bytes


class FilesystemError(ValueError):
    pass


class DockerFilesystem:
    def __init__(self, runner, max_file_bytes: int = 2 * 1024 * 1024) -> None:
        self.runner = runner
        self.max_file_bytes = max_file_bytes

    @property
    def container(self):
        container = self.runner.container
        if container is None:
            raise FilesystemError("Sandbox is not running.")
        return container

    def resolve(self, value: str) -> str:
        if "\x00" in str(value):
            raise FilesystemError("Invalid path.")
        relative = posixpath.normpath("/" + str(value or "/")).lstrip("/")
        root = posixpath.normpath(self.runner.policy["working_dir"])
        target = posixpath.normpath(posixpath.join(root, relative))
        if target != root and not target.startswith(root + "/"):
            raise FilesystemError("Path escapes the workspace.")
        return target

    def list(self, path: str) -> dict:
        target = self.resolve(path)
        result = self.container.exec_run(
            ["find", target, "-mindepth", "1", "-maxdepth", "1", "-print0"]
        )
        if result.exit_code != 0:
            raise FilesystemError("Directory does not exist.")
        entries = []
        for raw in result.output.split(b"\0"):
            if not raw:
                continue
            absolute = raw.decode("utf-8", errors="replace")
            stat = self.container.exec_run(["stat", "-c", "%F\t%s", absolute])
            if stat.exit_code != 0:
                continue
            kind, _, raw_size = stat.output.decode(errors="replace").strip().partition("\t")
            entries.append({
                "name": posixpath.basename(absolute),
                "path": "/" + posixpath.relpath(absolute, self.runner.policy["working_dir"]),
                "type": "directory" if "directory" in kind else "file",
                "size": int(raw_size or 0),
            })
        entries.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
        return {"ok": True, "entries": entries}

    def read(self, path: str) -> dict:
        target = self.resolve(path)
        chunks, stat = self.container.get_archive(target)
        size = int(stat.get("size") or 0)
        if size > self.max_file_bytes:
            raise FilesystemError("File is too large to open in the editor.")
        data = extract_single_file(chunks)
        if len(data) > self.max_file_bytes:
            raise FilesystemError("File is too large to open in the editor.")
        try:
            content = data.decode("utf-8")
            return {"ok": True, "content": content, "encoding": "utf-8"}
        except UnicodeDecodeError:
            return {
                "ok": True,
                "content": base64.b64encode(data).decode("ascii"),
                "encoding": "base64",
            }

    def write(self, path: str, content: str, encoding: str = "utf-8") -> dict:
        target = self.resolve(path)
        if target == self.runner.policy["working_dir"]:
            raise FilesystemError("A file path is required.")
        try:
            data = base64.b64decode(content, validate=True) if encoding == "base64" else content.encode()
        except Exception as exc:
            raise FilesystemError("Invalid file content.") from exc
        if len(data) > self.max_file_bytes:
            raise FilesystemError("File exceeds the write limit.")
        parent = posixpath.dirname(target)
        mkdir = self.container.exec_run(["mkdir", "-p", parent])
        if mkdir.exit_code != 0:
            raise FilesystemError("Parent directory could not be created.")
        if not self.container.put_archive(parent, tar_bytes(posixpath.basename(target), data)):
            raise FilesystemError("File could not be written.")
        return {"ok": True, "size": len(data)}

    def mkdir(self, path: str) -> dict:
        target = self.resolve(path)
        if target == self.runner.policy["working_dir"]:
            return {"ok": True}
        parent = posixpath.dirname(target)
        if not self.container.put_archive(
            parent, directory_tar_bytes(posixpath.basename(target))
        ):
            raise FilesystemError("Directory could not be created.")
        return {"ok": True}

    def rename(self, old: str, new: str) -> dict:
        source = self.resolve(old)
        target = self.resolve(new)
        if source == self.runner.policy["working_dir"]:
            raise FilesystemError("Workspace root cannot be renamed.")
        self.container.exec_run(["mkdir", "-p", posixpath.dirname(target)])
        result = self.container.exec_run(["mv", source, target])
        if result.exit_code != 0:
            raise FilesystemError("Path could not be renamed.")
        return {"ok": True}

    def delete(self, path: str) -> dict:
        target = self.resolve(path)
        if target == self.runner.policy["working_dir"]:
            raise FilesystemError("Workspace root cannot be deleted.")
        result = self.container.exec_run(["rm", "-rf", target])
        if result.exit_code != 0:
            raise FilesystemError("Path could not be deleted.")
        return {"ok": True}

    def handle(self, payload: dict) -> dict:
        op = payload.get("op")
        try:
            if op == "list":
                return self.list(payload.get("path", "/"))
            if op == "read":
                return self.read(payload.get("path", ""))
            if op == "write":
                return self.write(
                    payload.get("path", ""),
                    payload.get("content", ""),
                    payload.get("encoding", "utf-8"),
                )
            if op == "mkdir":
                return self.mkdir(payload.get("path", ""))
            if op == "rename":
                return self.rename(payload.get("old", ""), payload.get("new", ""))
            if op == "delete":
                return self.delete(payload.get("path", ""))
            return {"ok": False, "error": "Unsupported filesystem operation."}
        except (FilesystemError, FileNotFoundError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            return {"ok": False, "error": "Filesystem operation failed."}
