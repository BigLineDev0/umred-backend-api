from rest_framework_simplejwt.views import TokenObtainPairView
from .serializers import UmredTokenObtainPairSerializer, RegisterSerializer

from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

class UmredTokenObtainPairView(TokenObtainPairView):
    serializer_class = UmredTokenObtainPairSerializer
    

class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        refresh = RefreshToken.for_user(user)
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'role': user.role,
            'nom': user.nom,
            'prenom': user.prenom,
        }, status=status.HTTP_201_CREATED)