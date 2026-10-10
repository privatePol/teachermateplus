from functools import wraps

from django.core.exceptions import ValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

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


@require_http_methods(["GET"])
@access("host")
def host(request, public_id):
    scope = request.quitizz_scope
    session = get_object_or_404(QuiTizzSession.objects.filter(tenant_id=scope["tenant_id"], campus_id=scope["campus_id"], host=scope["user"]), public_id=public_id)
    return render(request, "quitizz/host.html", {"session": session, "questions": session.questions.all()})
