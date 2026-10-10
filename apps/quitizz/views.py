from functools import wraps

from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing

from . import gameplay
from .access import require_access
from .forms import QuestionForm, QuiTizzForm
from .models import QuiTizzSession
from .services import CONTENT_FIELDS, QuiTizzService, StaleRevision


def access(capability=None):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            scope = getattr(request, "scope", {})
            request.quitizz_scope = {"user": request.user, "tenant_id": scope.get("tenant_id"), "campus_id": scope.get("campus_id")}
            request.quitizz_caps = require_access(**request.quitizz_scope, capability=capability)
            try:
                return view(request, *args, **kwargs)
            except ValidationError as exc:
                return render(request, "quitizz/error.html", {"errors": exc.messages}, status=409 if isinstance(exc, StaleRevision) else 400)
        return wrapped
    return decorate


def owned(request, public_id):
    return get_object_or_404(QuiTizzService.owned(**request.quitizz_scope), public_id=public_id)


def mutation_args(request, public_id):
    return {**request.quitizz_scope, "public_id": public_id, "revision": request.POST.get("revision"), "request": request}


@require_http_methods(["GET"])
@access()
def quitizz_list(request):
    quizzes = QuiTizzService.owned(**request.quitizz_scope).select_related("owner", "campus").annotate(question_count=Count("questions"))
    return render(request, "quitizz/list.html", {"quizzes": quizzes, "caps": request.quitizz_caps})


@require_http_methods(["GET", "POST"])
@access("manage")
def create(request):
    form = QuiTizzForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = QuiTizzService.create(**request.quitizz_scope, title=form.cleaned_data["title"], request=request)
        return redirect("quitizz:edit", public_id=obj.public_id)
    return render(request, "quitizz/form.html", {"form": form}, status=400 if request.method == "POST" else 200)


@require_http_methods(["GET", "POST"])
@access("manage")
def edit(request, public_id):
    obj = owned(request, public_id)
    form = QuiTizzForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        QuiTizzService.edit(**mutation_args(request, public_id), title=form.cleaned_data["title"])
        return redirect("quitizz:edit", public_id=public_id)
    return render(request, "quitizz/form.html", {"form": form, "quiz": obj, "questions": obj.questions.all(), "caps": request.quitizz_caps}, status=400 if request.method == "POST" else 200)


@require_http_methods(["GET", "POST"])
@access("manage")
def question_edit(request, public_id, question_id=None):
    obj = owned(request, public_id)
    question = get_object_or_404(obj.questions, pk=question_id) if question_id else None
    form = QuestionForm(request.POST or None, instance=question)
    if request.method == "POST" and form.is_valid():
        QuiTizzService.question_save(**mutation_args(request, public_id), question_id=question_id,
            content={name: form.cleaned_data[name] for name in CONTENT_FIELDS})
        return redirect("quitizz:edit", public_id=public_id)
    return render(request, "quitizz/question.html", {"form": form, "quiz": obj, "question": question}, status=400 if request.method == "POST" else 200)


@require_http_methods(["GET", "POST"])
@access("manage")
def question_delete(request, public_id, question_id):
    obj = owned(request, public_id)
    question = get_object_or_404(obj.questions, pk=question_id)
    if request.method == "POST":
        QuiTizzService.question_delete(**mutation_args(request, public_id), question_id=question_id)
        return redirect("quitizz:edit", public_id=public_id)
    return render(request, "quitizz/delete.html", {"quiz": obj, "question": question})


@require_POST
@access("manage")
def reorder(request, public_id):
    QuiTizzService.reorder(**mutation_args(request, public_id), question_ids=request.POST.getlist("question_ids"))
    return redirect("quitizz:edit", public_id=public_id)


@require_POST
@access("manage")
def archive(request, public_id):
    if request.POST.get("archived") not in {"0", "1"}:
        raise ValidationError("Select archive or reactivate.")
    QuiTizzService.archive(**mutation_args(request, public_id), archived=request.POST["archived"] == "1")
    return redirect("quitizz:list")


@require_http_methods(["GET", "POST"])
@access("host")
def launch(request, public_id):
    obj = owned(request, public_id)
    if request.method == "POST":
        session = QuiTizzService.launch(**mutation_args(request, public_id))
        return redirect("quitizz:host", public_id=session.public_id)
    return render(request, "quitizz/launch.html", {"quiz": obj, "questions": obj.questions.all()})


def owned_session(request, public_id):
    scope = request.quitizz_scope
    return get_object_or_404(QuiTizzSession.objects.filter(tenant_id=scope["tenant_id"], campus_id=scope["campus_id"], host=scope["user"]), public_id=public_id)


@never_cache
@require_http_methods(["GET"])
@access("host")
def host(request, public_id):
    session = owned_session(request, public_id)
    state = gameplay.host_state(session)
    return render(request, "quitizz/host.html", {"session": session, "questions": session.questions.all(),
        "participants": state["participants"], "participant_count": state["participant_count"],
        "answered_count": state["answered_count"]})


@never_cache
@require_POST
@access("host")
def host_command(request, public_id):
    gameplay.command(**request.quitizz_scope, public_id=public_id, version=request.POST.get("version"),
        action=request.POST.get("action"), participant_id=request.POST.get("participant"), request=request)
    return redirect("quitizz:host", public_id=public_id)


@never_cache
@require_http_methods(["GET"])
@access("host")
def host_state(request, public_id):
    session = owned_session(request, public_id)
    try:
        gameplay.available(session)
    except gameplay.Unavailable as exc:
        return JsonResponse({"error": exc.messages[0]}, status=404)
    return JsonResponse(gameplay.host_state(session))


@never_cache
@require_http_methods(["GET"])
@access("host")
def projector(request, public_id):
    session = owned_session(request, public_id)
    return render(request, "quitizz/projector.html", {"session": session})


@never_cache
@require_http_methods(["GET"])
@access("host")
def projector_state(request, public_id):
    session = owned_session(request, public_id)
    try:
        gameplay.available(session)
    except gameplay.Unavailable as exc:
        return JsonResponse({"error": exc.messages[0]}, status=404)
    return JsonResponse(gameplay.presentation_state(session))


@never_cache
@require_http_methods(["GET"])
@access("host")
def host_qr(request, public_id):
    session = owned_session(request, public_id)
    url = request.build_absolute_uri(reverse("quitizz:play", kwargs={"public_id": public_id}))
    qr = QrCodeWidget(f"{url}#{gameplay.capability(session)}")
    x1, y1, x2, y2 = qr.getBounds()
    size = 320
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(qr)
    return HttpResponse(renderSVG.drawToString(drawing), content_type="image/svg+xml")
