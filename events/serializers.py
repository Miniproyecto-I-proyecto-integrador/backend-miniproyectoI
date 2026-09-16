from rest_framework import serializers
from .models import Activity, Subtask, DailyCapacity

class SubtaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subtask
        fields = '__all__'

class ActivitySerializer(serializers.ModelSerializer):
    subtasks = SubtaskSerializer(many=True, read_only=True)

    class Meta:
        model = Activity
        fields = '__all__'

class DailyCapacitySerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyCapacity
        fields = '__all__'