from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter
from logs.api import PrimaryLogViewSet, SecondaryLogViewSet

from mock_logs.users.api.views import UserViewSet

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

router.register("users", UserViewSet)

router.register(r"Primary-logs", PrimaryLogViewSet)
router.register(r"Secondary-logs", SecondaryLogViewSet)


app_name = "api"
urlpatterns = router.urls
