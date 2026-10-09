"""Sign-in, first-run setup, optional sign-up, and Settings (profile, API keys, users)."""
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse

from app import auth
from app.config import ALLOW_SIGNUP, COOKIE_SECURE, SESSION_DAYS

router = APIRouter()
templates = None        # set by app.api.routes (shared Jinja environment and filters)


def _render(request: Request, name: str, context: dict, status: int = 200):
    return templates.TemplateResponse(request, name, {"nav": None, **context}, status_code=status)


def _safe_next(target: Optional[str]) -> str:
    """Only same-site paths after sign-in (no open redirects)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target and "\n" not in target:
        return target
    return "/ui"


def _secure(request: Request) -> bool:
    if COOKIE_SECURE in ("true", "1", "yes"):
        return True
    if COOKIE_SECURE in ("false", "0", "no"):
        return False
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


def _signed_in(request: Request, user_id: int, target: str):
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(auth.SESSION_COOKIE, auth.create_session(user_id), max_age=int(SESSION_DAYS * 86400),
                        httponly=True, samesite="lax", secure=_secure(request), path="/")
    return response


def _forbidden(request: Request, message: str):
    return _render(request, "message.html", {"title": "Not allowed", "message": message, "back": "/ui"}, 403)


def _csrf(request: Request, token: Optional[str]) -> bool:
    return auth.csrf_ok(request.state.user, token)


def _client(request: Request) -> str:
    return request.client.host if request.client else ""


# ---------------------------------------------------------------- sign in / out

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/ui"):
    if auth.count_users() == 0:
        return RedirectResponse("/setup", status_code=303)
    if request.state.user:
        return RedirectResponse(_safe_next(next), status_code=303)
    return _render(request, "auth/login.html", {"next": _safe_next(next), "allow_signup": ALLOW_SIGNUP})


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form("/ui")):
    try:
        user = await run_in_threadpool(auth.authenticate, email, password, _client(request))
    except auth.AuthError as e:
        return _render(request, "auth/login.html", {"next": _safe_next(next), "email": email, "error": str(e),
                                                    "allow_signup": ALLOW_SIGNUP}, 401)
    target = "/settings?must_change=1" if user["must_change_password"] else _safe_next(next)
    return _signed_in(request, user["id"], target)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    if request.state.user and not _csrf(request, csrf_token):
        return _forbidden(request, "Your session changed. Reload the page and try again.")
    auth.end_session(request.cookies.get(auth.SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


# ---------------------------------------------------------------- first run / sign-up

@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if auth.count_users():
        return RedirectResponse("/login", status_code=303)
    return _render(request, "auth/setup.html", {})


@router.post("/setup", response_class=HTMLResponse)
def setup(request: Request, name: str = Form(""), email: str = Form(""), password: str = Form(""), confirm: str = Form("")):
    if auth.count_users():
        return RedirectResponse("/login", status_code=303)
    try:
        if password != confirm:
            raise auth.AuthError("The passwords do not match.")
        user = auth.create_first_admin(email, name, password)
    except auth.AuthError as e:
        return _render(request, "auth/setup.html", {"name": name, "email": email, "error": str(e)}, 400)
    return _signed_in(request, user["id"], "/ui")


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    if not ALLOW_SIGNUP:
        return _render(request, "message.html", {"title": "Ask an admin for an account",
                                                 "message": "Accounts on this server are created by its admins.",
                                                 "back": "/login"}, 404)
    return _render(request, "auth/signup.html", {})


@router.post("/signup", response_class=HTMLResponse)
def signup(request: Request, name: str = Form(""), email: str = Form(""), password: str = Form(""), confirm: str = Form("")):
    if not ALLOW_SIGNUP:
        return RedirectResponse("/login", status_code=303)
    try:
        if password != confirm:
            raise auth.AuthError("The passwords do not match.")
        user = auth.create_user(email, name, auth.VIEWER, password)
    except auth.AuthError as e:
        return _render(request, "auth/signup.html", {"name": name, "email": email, "error": str(e)}, 400)
    return _signed_in(request, user["id"], "/ui")


# ---------------------------------------------------------------- settings

def _settings(request: Request, tab: str, status: int = 200, **context):
    me = request.state.user
    data = {"tab": tab, "me": me, "keys": auth.list_api_keys(me.id)}
    if tab == "users" and me.is_admin:
        data["users"] = auth.list_users()
        data["all_keys"] = auth.list_api_keys()
    data.update(context)
    return _render(request, "settings.html", data, status)


@router.get("/settings", response_class=HTMLResponse)
def settings_profile(request: Request, must_change: int = 0):
    return _settings(request, "profile", must_change=bool(must_change) or request.state.user.must_change_password)


@router.post("/settings/password", response_class=HTMLResponse)
def change_password(request: Request, current: str = Form(""), password: str = Form(""), confirm: str = Form(""),
                    csrf_token: str = Form("")):
    me = request.state.user
    if not _csrf(request, csrf_token):
        return _forbidden(request, "Your session changed. Reload the page and try again.")
    try:
        if password != confirm:
            raise auth.AuthError("The new passwords do not match.")
        auth.change_own_password(me.id, current, password)
    except auth.AuthError as e:
        return _settings(request, "profile", 400, error=str(e), must_change=me.must_change_password)
    # changing the password signs out every session: start a fresh one here
    return _signed_in(request, me.id, "/settings?saved=password")


@router.get("/settings/api-keys", response_class=HTMLResponse)
def settings_keys(request: Request):
    return _settings(request, "keys")


@router.post("/settings/api-keys", response_class=HTMLResponse)
def create_key(request: Request, name: str = Form(""), csrf_token: str = Form("")):
    if not _csrf(request, csrf_token):
        return _forbidden(request, "Your session changed. Reload the page and try again.")
    created = auth.create_api_key(request.state.user.id, name)
    return _settings(request, "keys", new_key=created)


@router.post("/settings/api-keys/{key_id}/revoke")
def revoke_key(request: Request, key_id: int, csrf_token: str = Form(""), back: str = Form("/settings/api-keys")):
    if not _csrf(request, csrf_token):
        return _forbidden(request, "Your session changed. Reload the page and try again.")
    auth.revoke_api_key(key_id, request.state.user)
    return RedirectResponse(_safe_next(back), status_code=303)


@router.get("/settings/users", response_class=HTMLResponse)
def settings_users(request: Request):
    if not request.state.user.is_admin:
        return _forbidden(request, "Only admins manage users.")
    return _settings(request, "users")


@router.post("/settings/users", response_class=HTMLResponse)
def add_user(request: Request, name: str = Form(""), email: str = Form(""), role: str = Form(auth.VIEWER),
             csrf_token: str = Form("")):
    if not request.state.user.is_admin:
        return _forbidden(request, "Only admins manage users.")
    if not _csrf(request, csrf_token):
        return _forbidden(request, "Your session changed. Reload the page and try again.")
    password = auth.generate_password()
    try:
        user = auth.create_user(email, name, role, password, must_change_password=True)
    except auth.AuthError as e:
        return _settings(request, "users", 400, error=str(e), form={"name": name, "email": email, "role": role})
    return _settings(request, "users", issued={"email": user["email"], "password": password, "new": True})


@router.post("/settings/users/{user_id}", response_class=HTMLResponse)
def edit_user(request: Request, user_id: int, action: str = Form(""), role: str = Form(""), csrf_token: str = Form("")):
    me = request.state.user
    if not me.is_admin:
        return _forbidden(request, "Only admins manage users.")
    if not _csrf(request, csrf_token):
        return _forbidden(request, "Your session changed. Reload the page and try again.")
    target = auth.get_user(user_id)
    if target is None:
        return _settings(request, "users", 404, error="No such user.")
    try:
        if action == "role":
            auth.update_user(user_id, role=role)
        elif action == "deactivate":
            if user_id == me.id:
                raise auth.AuthError("You cannot deactivate your own account.")
            auth.update_user(user_id, active=False)
        elif action == "activate":
            auth.update_user(user_id, active=True)
        elif action == "reset-password":
            password = auth.generate_password()
            auth.set_password(user_id, password, must_change=True)
            return _settings(request, "users", issued={"email": target["email"], "password": password, "new": False})
        else:
            raise auth.AuthError("Unknown action.")
    except auth.AuthError as e:
        return _settings(request, "users", 400, error=str(e))
    return RedirectResponse("/settings/users", status_code=303)
