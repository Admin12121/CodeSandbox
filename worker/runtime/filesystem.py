from __future__ import annotations

import base64
import posixpath
import re

from .artifacts import directory_tar_bytes, extract_single_file, tar_bytes


class FilesystemError(ValueError):
    pass


class DockerFilesystem:
    def __init__(
        self,
        runner,
        max_file_bytes: int = 2 * 1024 * 1024,
        max_preview_bytes: int = 512 * 1024,
    ) -> None:
        self.runner = runner
        self.max_file_bytes = max_file_bytes
        self.max_preview_bytes = max_preview_bytes

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

    def _interactive_user(self) -> str | None:
        return self.runner._terminal_user()

    def _interactive_owner(self) -> tuple[int, int]:
        user = str(self._interactive_user() or "")
        numeric = re.fullmatch(r"([0-9]+)(?::([0-9]+))?", user)
        if numeric:
            uid = int(numeric.group(1))
            return uid, int(numeric.group(2) or uid)
        if not user:
            return 0, 0
        uid_result = self.container.exec_run(["id", "-u"], user=user)
        gid_result = self.container.exec_run(["id", "-g"], user=user)
        if uid_result.exit_code != 0 or gid_result.exit_code != 0:
            raise FilesystemError("Sandbox user identity could not be resolved.")
        return int(uid_result.output.strip()), int(gid_result.output.strip())

    def list(self, path: str) -> dict:
        target = self.resolve(path)
        # Do not depend on `find`/`stat`: intentionally small runtime images may
        # provide only /bin/sh. NUL-delimited shell globs also preserve spaces,
        # tabs and newlines in filenames.
        script = r"""
root=$1
[ -d "$root" ] || exit 2
for entry in "$root"/* "$root"/.[!.]* "$root"/..?*; do
  [ -e "$entry" ] || [ -L "$entry" ] || continue
  if [ -d "$entry" ]; then kind=directory; else kind=file; fi
  name=${entry##*/}
  printf '%s\0%s\0' "$kind" "$name"
done
"""
        result = self.container.exec_run(
            ["/bin/sh", "-c", script, "codesandbox-fs-list", target],
            user=self._interactive_user(),
        )
        if result.exit_code == 2:
            raise FilesystemError("Directory does not exist.")
        if result.exit_code != 0:
            raise FilesystemError("Directory could not be listed.")

        fields = result.output.split(b"\0")
        entries = []
        for index in range(0, len(fields) - 1, 2):
            if not fields[index + 1]:
                continue
            kind = fields[index].decode("utf-8", errors="replace")
            name = fields[index + 1].decode("utf-8", errors="replace")
            absolute = posixpath.join(target, name)
            entries.append({
                "name": name,
                "path": "/" + posixpath.relpath(absolute, self.runner.policy["working_dir"]),
                "type": "directory" if kind == "directory" else "file",
                "size": 0,
            })
        entries.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
        return {"ok": True, "entries": entries}

    def read(self, path: str) -> dict:
        target = self.resolve(path)
        chunks, stat = self.container.get_archive(target)
        size = int(stat.get("size") or 0)
        if size > self.max_file_bytes:
            return self._preview_large_file(target, size)
        data = extract_single_file(chunks)
        if len(data) > self.max_file_bytes:
            return self._preview_large_file(target, len(data))
        return self._content_response(data)

    def _content_response(self, data: bytes, **extra) -> dict:
        try:
            content = data.decode("utf-8")
            return {"ok": True, "content": content, "encoding": "utf-8", **extra}
        except UnicodeDecodeError:
            return {
                "ok": True,
                "content": base64.b64encode(data).decode("ascii"),
                "encoding": "base64",
                **extra,
            }

    def _preview_large_file(self, target: str, size: int) -> dict:
        script = r"""
limit=$1
path=$2
[ -f "$path" ] || exit 2
head -c "$limit" "$path"
"""
        result = self.container.exec_run(
            ["/bin/sh", "-c", script, "codesandbox-fs-preview", str(self.max_preview_bytes), target],
            user=self._interactive_user(),
        )
        if result.exit_code == 2:
            raise FilesystemError("File does not exist.")
        if result.exit_code != 0:
            raise FilesystemError("Large file preview is unavailable.")
        return self._content_response(
            bytes(result.output),
            truncated=True,
            read_only=True,
            size=size,
            preview_bytes=len(result.output),
        )

    def download(self, path: str) -> dict:
        target = self.resolve(path)
        chunks, stat = self.container.get_archive(target)
        size = int(stat.get("size") or 0)
        max_download_bytes = int(self.runner.policy.get("max_upload_bytes") or 64 * 1024 * 1024)
        max_download_bytes = max(1, min(max_download_bytes, 128 * 1024 * 1024))
        if size > max_download_bytes:
            raise FilesystemError("File is too large to download from the IDE.")
        data = extract_single_file(chunks)
        if len(data) > max_download_bytes:
            raise FilesystemError("File is too large to download from the IDE.")
        return {
            "ok": True,
            "content": base64.b64encode(data).decode("ascii"),
            "encoding": "base64",
            "size": len(data),
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
        mkdir = self.container.exec_run(
            ["mkdir", "-p", parent], user=self._interactive_user()
        )
        if mkdir.exit_code != 0:
            raise FilesystemError("Parent directory could not be created.")
        uid, gid = self._interactive_owner()
        if not self.container.put_archive(
            parent,
            tar_bytes(posixpath.basename(target), data, uid=uid, gid=gid),
        ):
            raise FilesystemError("File could not be written.")
        return {"ok": True, "size": len(data)}

    def mkdir(self, path: str) -> dict:
        target = self.resolve(path)
        if target == self.runner.policy["working_dir"]:
            return {"ok": True}
        parent = posixpath.dirname(target)
        ensure_parent = self.container.exec_run(
            ["mkdir", "-p", parent], user=self._interactive_user()
        )
        if ensure_parent.exit_code != 0:
            raise FilesystemError("Parent directory could not be created.")
        uid, gid = self._interactive_owner()
        if not self.container.put_archive(
            parent,
            directory_tar_bytes(posixpath.basename(target), uid=uid, gid=gid),
        ):
            raise FilesystemError("Directory could not be created.")
        return {"ok": True}

    def rename(self, old: str, new: str) -> dict:
        source = self.resolve(old)
        target = self.resolve(new)
        if source == self.runner.policy["working_dir"]:
            raise FilesystemError("Workspace root cannot be renamed.")
        self.container.exec_run(
            ["mkdir", "-p", posixpath.dirname(target)],
            user=self._interactive_user(),
        )
        result = self.container.exec_run(
            ["mv", source, target], user=self._interactive_user()
        )
        if result.exit_code != 0:
            raise FilesystemError("Path could not be renamed.")
        return {"ok": True}

    def delete(self, path: str) -> dict:
        target = self.resolve(path)
        if target == self.runner.policy["working_dir"]:
            raise FilesystemError("Workspace root cannot be deleted.")
        result = self.container.exec_run(
            ["rm", "-rf", target], user=self._interactive_user()
        )
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
            if op == "download":
                return self.download(payload.get("path", ""))
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
