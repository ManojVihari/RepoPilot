import subprocess
import textwrap

import pytest

from docai.core.scanner import Scanner
from docai.plugins.java_spring.extractor import SpringExtractor
from docai.plugins.python_fastapi.extractor import FastAPIExtractor


CONTROLLER = """
@RestController
@RequestMapping("/users")
public class UserController {
    @GetMapping("/{id}")
    public UserDto getUser(@PathVariable Long id) {
        return userService.findUser(id);
    }
    @PostMapping("/create")
    public String createUser(@RequestBody UserRequest request) {
        userService.store(request);
        return "created";
    }
}
"""

SERVICE = """
@Service
public class UserService {
    public UserDto findUser(Long id) { return repo.findById(id); }
    public void store(UserRequest r) { validate(r); }
    private void validate(UserRequest r) { }
}
"""

USER_REQUEST = """
public class UserRequest {
    @NotNull
    private String name;
    @Size(min = 2, max = 10)
    private String email;
}
"""

FASTAPI_APP = """
from fastapi import APIRouter, HTTPException
router = APIRouter()

@router.get("/items/{item_id}")
def get_item(item_id: int, q: str = None):
    if item_id < 0:
        raise HTTPException(status_code=404, detail="missing")
    return load_item(item_id)

@router.post("/items")
def create_item(name: str):
    return JSONResponse(status_code=201, content={})
"""


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def write(repo, path, content):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(content))


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "test")

    write(tmp_path, "src/controller/UserController.java", CONTROLLER)
    write(tmp_path, "src/service/UserService.java", SERVICE)
    write(tmp_path, "src/dto/UserRequest.java", USER_REQUEST)
    write(tmp_path, "src/dto/UserRequestAudit.java", "public class UserRequestAudit { private String auditor; }")
    write(tmp_path, "src/dto/UserDto.java", "public class UserDto { private Long id; }")
    write(tmp_path, "api/items.py", FASTAPI_APP)
    write(tmp_path, "venv/lib/vendored.py", FASTAPI_APP)

    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")

    # Commit 2: DTO validation and a service method change
    write(tmp_path, "src/dto/UserRequest.java", USER_REQUEST.replace("@NotNull", "@NotEmpty"))
    write(tmp_path, "src/service/UserService.java", SERVICE.replace("{ }", "{ int x = 1; }"))
    git(tmp_path, "commit", "-qam", "change dto")

    # Commit 3: only the FastAPI app
    write(tmp_path, "api/items.py", FASTAPI_APP + "\n# touched\n")
    git(tmp_path, "commit", "-qam", "change fastapi")

    return tmp_path


def by_function(result):
    return {r["function"]: r for r in result["routes"]}


def test_changed_files_follow_the_given_commit(repo):
    scanner = Scanner()

    assert scanner.get_changed_files(str(repo), "HEAD") == ["api/items.py"]
    assert sorted(scanner.get_changed_files(str(repo), "HEAD~1")) == [
        "src/dto/UserRequest.java",
        "src/service/UserService.java",
    ]


def test_root_commit_falls_back_to_tracked_files(repo):
    files = Scanner().get_changed_files(str(repo), "HEAD~2")

    assert "src/controller/UserController.java" in files
    assert "api/items.py" in files


def test_scan_only_reports_routes_impacted_by_commit(repo):
    result = Scanner().scan(str(repo), "HEAD")

    assert set(result["frameworks"]) == {"spring", "fastapi"}
    assert set(by_function(result)) == {"get_item", "create_item"}


def test_scan_spring_impact_and_breaking_changes(repo):
    routes = by_function(Scanner().scan(str(repo), "HEAD~1"))

    # findUser changed in the service -> getUser impacted through the call graph
    assert set(routes["getUser"]["impact"]) == {"findUser", "findById"}

    create = routes["createUser"]
    assert {"type": "VALIDATION_ADDED", "field": "name", "rule": "notEmpty"} in create["breaking_changes"]


def test_dto_schema_matches_exact_class_name(repo):
    extractor = SpringExtractor()
    schema = extractor.extract_dto_schema(str(repo), "UserRequest")

    # UserRequestAudit.java must not leak into UserRequest
    assert set(schema) == {"name", "email"}
    assert schema["email"]["validation"] == {"min": 2, "max": 10}


def test_fastapi_errors_only_come_from_http_exception(repo):
    routes = {r["function"]: r for r in FastAPIExtractor().extract_from_file(str(repo / "api/items.py"))}

    assert routes["get_item"]["errors"] == [404]
    assert routes["create_item"]["errors"] == []


def test_expand_impact_handles_cycles():
    graph = {"a": ["b"], "b": ["c", "a"], "c": ["d"]}

    assert sorted(FastAPIExtractor().expand_impact("a", graph)) == ["b", "c", "d"]
    assert sorted(SpringExtractor().expand_impact("a", graph)) == ["a", "b", "c", "d"]
