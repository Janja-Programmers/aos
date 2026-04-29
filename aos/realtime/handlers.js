function aosRealtimeHandlers(socket) {
	socket.on("join_live_room", (data) => {
		const liveId = data?.live_id;

		if (!liveId) {
			socket.emit("aos_live_room_error", {
				message: "live_id is required",
			});
			return;
		}

		const room = `live:${liveId}`;

		socket.join(room);

		socket.emit("aos_live_room_joined", {
			room,
		});

		console.log(`[AOS] ${socket.id} joined ${room}`);
	});

	socket.on("leave_live_room", (data) => {
		const liveId = data?.live_id;

		if (!liveId) return;

		const room = `live:${liveId}`;

		socket.leave(room);

		socket.emit("aos_live_room_left", {
			room,
		});

		console.log(`[AOS] ${socket.id} left ${room}`);
	});
}

module.exports = aosRealtimeHandlers;
