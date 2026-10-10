from django.urls import path

from . import views

app_name = "quitizz"
urlpatterns = [
    path("faculty/quitizz/", views.quitizz_list, name="list"),
    path("faculty/quitizz/create/", views.create, name="create"),
    path("faculty/quitizz/<uuid:public_id>/edit/", views.edit, name="edit"),
    path("faculty/quitizz/<uuid:public_id>/questions/add/", views.question_edit, name="question_add"),
    path("faculty/quitizz/<uuid:public_id>/questions/<int:question_id>/edit/", views.question_edit, name="question_edit"),
    path("faculty/quitizz/<uuid:public_id>/questions/<int:question_id>/delete/", views.question_delete, name="question_delete"),
    path("faculty/quitizz/<uuid:public_id>/reorder/", views.reorder, name="reorder"),
    path("faculty/quitizz/<uuid:public_id>/archive/", views.archive, name="archive"),
    path("faculty/quitizz/<uuid:public_id>/launch/", views.launch, name="launch"),
    path("faculty/quitizz/sessions/<uuid:public_id>/host/", views.host, name="host"),
]
