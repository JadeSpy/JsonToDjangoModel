from django.db import models

class MainModel(models.Model):
	def fromJSON(json_data: dict,save=False):
		child_models = {}
		child_models["ItemsModel"] = [ItemsModel.fromJSON(item,save) for item in json_data["items"]]
		model_instance = MainModel()
		if save==True: model_instance.save()
		return model_instance,child_models

class ItemsModel(models.Model):
	main_model = models.ForeignKey(MainModel,on_delete=models.CASCADE)
	tag = models.CharField(max_length=16,null=False)
	name = models.CharField(max_length=32,null=False)
	role = models.CharField(max_length=8,null=False)
	lastSeen = models.CharField(max_length=32,null=False)
	expLevel = models.IntegerField(null=False)
	trophies = models.IntegerField(null=False)
	arena_id = models.IntegerField(null=False)
	arena_name = models.CharField(max_length=8,null=False)
	clanRank = models.IntegerField(null=False)
	previousClanRank = models.IntegerField(null=False)
	donations = models.IntegerField(null=False)
	donationsReceived = models.IntegerField(null=False)
	clanChestPoints = models.IntegerField(null=False)
	def fromJSON(json_data: dict,save=False):
		model_instance = MainModel(tag=json_data["tag"], name=json_data["name"], role=json_data["role"], lastSeen=json_data["lastSeen"], expLevel=json_data["expLevel"], trophies=json_data["trophies"], arena_id=json_data["id"], arena_name=json_data["name"], clanRank=json_data["clanRank"], previousClanRank=json_data["previousClanRank"], donations=json_data["donations"], donationsReceived=json_data["donationsReceived"], clanChestPoints=json_data["clanChestPoints"])
		if save==True: model_instance.save()
		return model_instance

