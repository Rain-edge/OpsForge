# OpsForge：巡检报告模型（补齐入库的 0003 —— 此前被 spug_api/.gitignore 的 migrations/ 规则漏掉）
# 字段与 models.py 的 InspectionReport 对齐（makemigrations --check 口径）

from django.db import migrations, models
import django.db.models.deletion
import libs.mixins
import libs.utils


class Migration(migrations.Migration):

    dependencies = [
        ('account', '__first__'),
        ('ansible_ops', '0002_remove_ansiblejob_playbook_id_playbook_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='InspectionReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('trigger', models.CharField(choices=[('manual', '手动触发'), ('scheduled', '定时巡检')], default='manual', max_length=20)),
                ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='account.user')),
                ('status', models.CharField(choices=[('running', '巡检中'), ('success', '完成'), ('partial', '部分完成（有主机不可达）'), ('failed', '失败')], default='running', max_length=20)),
                ('total_ok', models.IntegerField(default=0)),
                ('total_warn', models.IntegerField(default=0)),
                ('total_crit', models.IntegerField(default=0)),
                ('hosts_total', models.IntegerField(default=0)),
                ('hosts_unreachable', models.IntegerField(default=0)),
                ('html_path', models.CharField(default='', max_length=255)),
                ('xlsx_path', models.CharField(default='', max_length=255)),
                ('manifest_path', models.CharField(default='', max_length=255)),
                ('summary', models.TextField(default='{}')),
                ('created_at', models.CharField(default=libs.utils.human_datetime, max_length=20)),
                ('finished_at', models.CharField(max_length=20, null=True)),
            ],
            options={
                'db_table': 'inspection_reports',
                'ordering': ('-id',),
            },
            bases=(models.Model, libs.mixins.ModelMixin),
        ),
    ]
